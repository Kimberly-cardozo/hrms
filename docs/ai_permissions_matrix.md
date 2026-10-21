# SQL Agent Access Control Matrix

The SQL agent derives the role from the authenticated employee record. It never accepts a role from the request body. This matrix is enforced in `backend/app/services/ai/permissions.py` and by role-scoped temporary SQLite tables in `backend/app/services/ai/sql_guardrails.py`.

| AI capability | Employee | Manager | Admin |
|---|---|---|---|
| Ask own leave balance | Own rows | Own and direct reports | All rows |
| Ask another employee's leave balance | No | Direct reports only | Yes |
| View own project assignments | Own rows | Own and direct reports | All rows |
| View all project assignments | No | Direct reports only | Yes |
| Search employees by skill | Own skill records only | Direct reports only | Yes |
| Generate SQL over HR data | Scoped rows in allowed tables | Scoped rows in allowed tables | Broad allowed-table access |
| View raw SQL | No | Yes | Yes |
| Access payroll data | Own records | Own and direct reports | All employees (allowed fields only) |
| Access bank, PAN, password, date-of-birth, or profile-file columns | No | No | No |

All roles may query the shared project, department, and skill catalogs. The SQL table allowlist is `employees`, `projects`, `employee_projects`, `departments`, `skills`, `employee_skills`, `job_history`, `leave_balances`, `leave_requests`, `tickets`, and `payroll_records`. Manager team scope currently means the manager and direct reports; indirect reports and broader project membership are not included.

Payroll rows are scoped through `payroll_records.employee_id`: employees see only their own records, managers see their own and direct reports' records, and admins see all employees' records. Payroll amounts, deductions, and periods are available within that row scope. Bank account details, PAN identifiers, UAN, ESI numbers, passwords, date-of-birth, profile-file, and other protected identifiers remain blocked for every role. The `current_salary_usd` field on `employees` also remains blocked.

SQLGlot parses generated SQL as SQLite, requires a single query AST, and rejects unknown/qualified tables and protected columns before execution. SQLite read-only mode, role-scoped temporary tables, and the SQLite authorizer remain additional enforcement layers after parsing.

## HR Task Automation Matrix

The action agent receives only the tools allowlisted for the role from the authenticated employee record. It never writes to a database. Reads and mutations call the existing FastAPI endpoints with the current user's bearer token, so endpoint validation and authorization remain authoritative.

| HR task capability | Employee | Manager | Admin |
|---|---|---|---|
| Apply for own leave | Yes | Yes | Yes |
| Check own leave balance/requests | Yes | Yes | Yes |
| Approve/reject leave | No | Own and direct reports | All requests |
| Create/check own tickets | Yes | Yes | Yes |
| Assign tickets/update ticket status | No | Yes | Yes |
| Create announcements | No | Yes | Yes |
| Assign employees to projects | No | Yes | Yes |
| Create project catalog entries | No | No | Yes |
| List HR policies | Yes | Yes | Yes |
| Upload HR policy text/Markdown | No | No | Yes |
| Summarize an existing HR policy | No | No | Yes |
| Deactivate/reactivate employees | No | No | Yes |

Manager team-only leave visibility and approval are enforced in the action-agent API-tool layer: it filters pending requests using the authenticated org tree, and it verifies team membership again before approve/reject API calls. The underlying manager/admin leave endpoints retain their general manager/admin authorization; this AI-specific team restriction does not change other API consumers.

Mutating API calls require explicit user confirmation. The agent first returns a signed, user- and role-bound confirmation token; only a subsequent authenticated confirmation request executes the selected API tool. Tokens expire after five minutes and cannot be reused. Cancelling a proposal makes no API call. Project/employee/skill lookups are handed off to Query Assistant rather than answered by the action agent.

General policy questions are handed off to HR Policies. Admin policy summaries use the authenticated HR Policy download API before sending document text to the model; paths and checksum metadata are not included. The action agent does not execute SQL or access a database session.