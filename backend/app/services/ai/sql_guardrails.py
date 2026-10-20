import re
import sqlite3
import time
from pathlib import Path

from sqlglot import exp, parse
from sqlglot.errors import ParseError
from sqlalchemy.engine import make_url

from app.models.enums import Role
from app.services.ai.permissions import (
    SQL_ACCESS_CONTROL_MATRIX,
    SQL_ALLOWED_TABLES,
    SQL_BLOCKED_COLUMNS,
    SQL_TABLES_BY_ROLE,
    row_scope_clause,
)

ALLOWED_TABLES = SQL_ALLOWED_TABLES
BLOCKED_COLUMNS = SQL_BLOCKED_COLUMNS
MAX_RESULT_ROWS = 100
MAX_QUERY_SECONDS = 3
_FORBIDDEN_TOKENS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|PRAGMA|ATTACH|DETACH|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)
_BLOCKED_COLUMN_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(column) for column in BLOCKED_COLUMNS) + r")\b",
    re.IGNORECASE,
)


class SQLGuardrailError(ValueError):
    def __init__(
        self,
        message: str,
        code: str = "QUERY_REJECTED",
        table_name: str | None = None,
        restricted_columns: tuple[str, ...] = (),
    ):
        super().__init__(message)
        self.code = code
        self.table_name = table_name
        self.restricted_columns = restricted_columns


def clean_generated_sql(
    raw_sql: str,
    role: Role = Role.ADMIN,
    named_employee_matches: list[tuple[int, str]] | None = None,
    named_employee_subject_name: str | None = None,
) -> str:
    sql = raw_sql.strip()
    fenced = re.fullmatch(r"```(?:sql)?\s*(.*?)\s*```", sql, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        sql = fenced.group(1).strip()
    if sql.lower().startswith("sqlquery:"):
        sql = sql.split(":", 1)[1].strip()

    if not sql or len(sql) > 8000:
        raise SQLGuardrailError("The generated query is empty or too long.")
    if "--" in sql or "/*" in sql or "*/" in sql:
        raise SQLGuardrailError("SQL comments are not allowed.")
    if ";" in sql.rstrip().rstrip(";"):
        raise SQLGuardrailError("Only one SQL statement is allowed.")
    sql = sql.rstrip().removesuffix(";").strip()
    if not re.match(r"^SELECT\b", sql, flags=re.IGNORECASE):
        raise SQLGuardrailError("Only SELECT queries are allowed.")
    if _FORBIDDEN_TOKENS.search(sql):
        raise SQLGuardrailError("The query contains a disallowed SQL operation.")
    blocked_columns = tuple(
        sorted(
            column
            for column in BLOCKED_COLUMNS
            if re.search(rf"\b{re.escape(column)}\b", sql, flags=re.IGNORECASE)
        )
    )
    if blocked_columns:
        raise SQLGuardrailError(
            "The query references restricted fields.",
            code="RESTRICTED_FIELD",
            restricted_columns=blocked_columns,
        )

    try:
        statements = [statement for statement in parse(sql, read="sqlite") if statement is not None]
    except ParseError as error:
        raise SQLGuardrailError("The generated SQL could not be parsed safely.") from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise SQLGuardrailError("Only one parsed SELECT query is allowed.")

    statement = statements[0]
    cte_names = {cte.alias_or_name.casefold() for cte in statement.find_all(exp.CTE)}
    allowed_tables = SQL_TABLES_BY_ROLE[role]
    for table in statement.find_all(exp.Table):
        table_name = table.name.casefold()
        if table_name in cte_names:
            continue
        if table.db or table.catalog or table_name not in allowed_tables:
            raise SQLGuardrailError(
                "The query references a table outside this role's allowed schema.",
                code="DISALLOWED_TABLE",
                table_name=table_name,
            )
    for column in statement.find_all(exp.Column):
        if column.name.casefold() in SQL_BLOCKED_COLUMNS:
            raise SQLGuardrailError(
                "The query references restricted fields.",
                code="RESTRICTED_FIELD",
                restricted_columns=(column.name.casefold(),),
            )

    employee_aliases = {
        table.alias_or_name.casefold()
        for table in statement.find_all(exp.Table)
        if table.name.casefold() == "employees"
    }
    employee_ids = {employee_id for employee_id, _ in named_employee_matches or []}
    rewritten_name_comparison = False
    named_subject_filter_found = False
    for comparison in statement.find_all(exp.EQ):
        for operand_name in ("this", "expression"):
            operand = comparison.args.get(operand_name)
            value_name = "expression" if operand_name == "this" else "this"
            value = comparison.args.get(value_name)
            if not isinstance(operand, exp.Column):
                continue
            is_employee_column = (
                operand.table.casefold() in employee_aliases
                if operand.table
                else bool(employee_aliases)
            )
            if (
                named_employee_subject_name
                and is_employee_column
                and operand.name.casefold() in {"id", "manager_id"}
                and isinstance(value, exp.Literal)
            ):
                employee_table = next(
                    table for table in statement.find_all(exp.Table)
                    if table.name.casefold() == "employees"
                    and (not operand.table or table.alias_or_name.casefold() == operand.table.casefold())
                )
                employee_name_column = exp.Collate(
                    this=exp.column("name", table=employee_table.alias_or_name),
                    expression=exp.Var(this="NOCASE"),
                )
                comparison.set(operand_name, employee_name_column)
                comparison.set(value_name, exp.Literal.string(named_employee_subject_name))
                named_subject_filter_found = True
                rewritten_name_comparison = True
                continue
            if operand.name.casefold() == "name" and is_employee_column:
                if named_employee_subject_name:
                    comparison.set(value_name, exp.Literal.string(named_employee_subject_name))
                    named_subject_filter_found = True
                if isinstance(value, exp.Literal) and value.is_string and named_employee_matches:
                    partial_name = value.this.casefold()
                    full_names = {
                        name
                        for _, name in named_employee_matches
                        if partial_name in {word.casefold() for word in re.findall(r"\w+", name)}
                    }
                    if len(full_names) == 1:
                        comparison.set(value_name, exp.Literal.string(next(iter(full_names))))
                comparison.set(
                    operand_name,
                    exp.Collate(this=operand.copy(), expression=exp.Var(this="NOCASE")),
                )
                rewritten_name_comparison = True
            elif (
                len(employee_ids) == 1
                and isinstance(value, exp.Literal)
                and not value.is_string
                and value.this.isdigit()
                and (
                    operand.name.casefold() == "employee_id"
                    or (operand.name.casefold() == "id" and is_employee_column)
                )
            ):
                comparison.set(value_name, exp.Literal.number(str(next(iter(employee_ids)))))
                rewritten_name_comparison = True

    if named_employee_subject_name and employee_aliases and not named_subject_filter_found:
        employee_table = next(
            table for table in statement.find_all(exp.Table)
            if table.name.casefold() == "employees"
        )
        name_filter = exp.EQ(
            this=exp.Collate(
                this=exp.column("name", table=employee_table.alias_or_name),
                expression=exp.Var(this="NOCASE"),
            ),
            expression=exp.Literal.string(named_employee_subject_name),
        )
        where = statement.args.get("where")
        if where:
            where.set("this", exp.and_(where.this, name_filter))
        else:
            statement.set("where", exp.Where(this=name_filter))
        rewritten_name_comparison = True

    return statement.sql(dialect="sqlite") if rewritten_name_comparison else sql


def _sqlite_file_uri(database_url: str) -> str:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise SQLGuardrailError("The SQL assistant currently requires a file-backed SQLite database.")
    database_path = Path(url.database)
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    return f"{database_path.resolve().as_uri()}?mode=ro"


def _row_scope(table: str, role: Role, employee_id: int) -> str | None:
    return row_scope_clause(table, role, employee_id)


def get_scoped_employee_matches(
    database_url: str,
    employee_id: int,
    role: Role,
    message: str,
) -> list[tuple[int, str]]:
    connection: sqlite3.Connection | None = None
    try:
        database_uri = _sqlite_file_uri(database_url)
        connection = sqlite3.connect(database_uri, uri=True, timeout=3)
        scope = _row_scope("employees", role, employee_id)
        where_clause = f" WHERE {scope}" if scope else ""
        rows = connection.execute(
            f'SELECT "id", "name" FROM main."employees"{where_clause}'
        ).fetchall()
        matches = []
        message_words = re.findall(r"\w+", message.casefold())
        for row_id, name in rows:
            if not isinstance(name, str) or not name.strip():
                continue
            name_words = re.findall(r"\w+", name.casefold())
            for start in range(len(message_words) - len(name_words) + 1):
                candidate_words = message_words[start : start + len(name_words)]
                if all(
                    actual == expected or actual == f"{expected}s"
                    for actual, expected in zip(candidate_words, name_words)
                ):
                    matches.append((int(row_id), name.strip()))
                    break
        matched_words = {
            employee_id: re.findall(r"\w+", name.casefold())
            for employee_id, name in matches
        }
        return [
            match
            for match in matches
            if not any(
                len(other_words) > len(matched_words[match[0]])
                and any(
                    other_words[index : index + len(matched_words[match[0]])]
                    == matched_words[match[0]]
                    for index in range(len(other_words) - len(matched_words[match[0]]) + 1)
                )
                for other_id, other_words in matched_words.items()
                if other_id != match[0]
            )
        ]
    except sqlite3.Error as error:
        raise SQLGuardrailError("The HRMS database is unavailable to the SQL assistant.") from error
    finally:
        if connection is not None:
            connection.close()


def _open_scoped_connection(database_url: str, employee_id: int, role: Role) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(_sqlite_file_uri(database_url), uri=True, timeout=3)
    except (OSError, sqlite3.Error, ValueError) as error:
        raise SQLGuardrailError("The HRMS database is unavailable to the SQL assistant.") from error

    try:
        connection.row_factory = sqlite3.Row
        for table in SQL_TABLES_BY_ROLE[role]:
            columns = connection.execute(f'PRAGMA main.table_info("{table}")').fetchall()
            if not columns:
                raise SQLGuardrailError("The HRMS database schema is incomplete for the SQL assistant.")
            safe_columns = [column["name"] for column in columns if column["name"].casefold() not in BLOCKED_COLUMNS]
            if not safe_columns:
                raise SQLGuardrailError("The HRMS database schema is incomplete for the SQL assistant.")

            projected = ", ".join(f'"{column}"' for column in safe_columns)
            scope = _row_scope(table, role, employee_id)
            where_clause = f" WHERE {scope}" if scope else ""
            connection.execute(
                f'CREATE TEMP TABLE "{table}" AS SELECT {projected} FROM main."{table}"{where_clause}'
            )

        def authorize(action, argument1, argument2, database_name, source):
            if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                table_name = (argument1 or "").casefold()
                column_name = (argument2 or "").casefold()
                source_name = (source or "").casefold()
                is_scoped_table = database_name in {None, "temp"} and table_name in SQL_TABLES_BY_ROLE[role] and not source_name
                if is_scoped_table and column_name not in BLOCKED_COLUMNS:
                    return sqlite3.SQLITE_OK
            elif action == sqlite3.SQLITE_FUNCTION:
                function_name = (argument2 or "").casefold()
                if function_name not in {"load_extension", "writefile", "readfile"}:
                    return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        connection.set_authorizer(authorize)
        return connection
    except Exception:
        connection.close()
        raise


def get_scoped_schema(database_url: str, employee_id: int, role: Role) -> str:
    connection = _open_scoped_connection(database_url, employee_id, role)
    try:
        definitions = []
        for table in SQL_TABLES_BY_ROLE[role]:
            cursor = connection.execute(f'SELECT * FROM temp."{table}" LIMIT 0')
            column_names = [description[0] for description in cursor.description or []]
            definitions.append(f"{table}({', '.join(column_names)})")
        return "\n".join(definitions)
    finally:
        connection.close()


def execute_scoped_select(database_url: str, employee_id: int, role: Role, sql: str) -> tuple[list[dict], bool]:
    safe_sql = clean_generated_sql(sql, role)
    connection = _open_scoped_connection(database_url, employee_id, role)
    started = time.monotonic()
    connection.set_progress_handler(lambda: int(time.monotonic() - started > MAX_QUERY_SECONDS), 1000)
    try:
        cursor = connection.execute(safe_sql)
        rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
        truncated = len(rows) > MAX_RESULT_ROWS
        serialized = []
        for row in rows[:MAX_RESULT_ROWS]:
            serialized.append(
                {
                    key: value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
                    for key, value in dict(row).items()
                }
            )
        return serialized, truncated
    except (sqlite3.DatabaseError, sqlite3.OperationalError) as error:
        if "interrupted" in str(error).casefold():
            raise SQLGuardrailError("The query exceeded the execution time limit.") from error
        raise SQLGuardrailError("The generated query could not be run safely.") from error
    finally:
        connection.close()