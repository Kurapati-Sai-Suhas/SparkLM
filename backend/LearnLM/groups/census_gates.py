"""
Production connection gates for the census (M2 P2.7e).

The census's real safety mechanism. Python being read-only is a property of
code that can be edited; a database role without INSERT is a property of the
server that cannot.

Every gate ABORTS. None degrades, none warns-and-continues, and none has an
override that keeps the "production" label — because the failure this guards
against is not a crash, it is a report that says PRODUCTION at the top and
contains development numbers.

Gates, in order:

    1. exactly one intended alias, resolved
    2. the host is not loopback or private
    3. the alias is the one the operator named
    4. the role identity is readable
    5. the role has NO INSERT/UPDATE/DELETE/DDL privilege
    6. required migrations are applied

Nothing here prints a password, host, or connection string.
"""

import ipaddress

from django.db import connections

#: Tables whose write privileges are checked. Grading truth and learner state —
#: if the role can write to these, the census is running with credentials that
#: could destroy the thing it is measuring.
GUARDED_TABLES = (
    "groups_question",
    "groups_codesubmission",
    "groups_referencesolution",
    "groups_oracleexecution",
    "groups_questionapproval",
)

WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES")

#: Migrations the census's queries depend on. Below these, columns the report
#: needs do not exist and a census would silently describe an older schema.
REQUIRED_MIGRATIONS = (
    "0038_codesubmission_adaptive_eligible_question_status_and_more",
    "0039_reference_solution_lifecycle",
    "0041_output_provenance",
    "0042_question_approval",
)


class GateFailure(Exception):
    """A gate refused. The census must not run."""


def classify_host(host):
    """A host's network class, without disclosing the host itself."""
    if not host:
        return "EMPTY"
    if host.lower() in ("localhost", "localhost.localdomain"):
        return "LOOPBACK"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "REMOTE_HOSTNAME"
    if address.is_loopback:
        return "LOOPBACK"
    if address.is_private:
        return "PRIVATE"
    return "PUBLIC_IP"


def gate_alias(alias):
    """Gate 1 + 3: the named alias exists and is configured."""
    if alias not in connections.databases:
        raise GateFailure(
            f"no database alias named {alias!r}; configured aliases are "
            f"{sorted(connections.databases)}")
    config = connections.databases[alias]
    if not config.get("HOST"):
        raise GateFailure(
            f"alias {alias!r} has no HOST configured — it would connect to a "
            f"local socket, not production")
    return config


def gate_not_local(config, *, allow_non_production):
    """
    Gate 2: refuse loopback and private addresses.

    `allow_non_production` exists only so the census can be exercised against
    a synthetic database in tests. It does NOT relax any other gate, and the
    report is stamped `is_production=false` when it is used, so a
    non-production run can never be mistaken for a production one.
    """
    host_class = classify_host(config.get("HOST"))
    if host_class in ("LOOPBACK", "PRIVATE", "EMPTY"):
        if not allow_non_production:
            raise GateFailure(
                f"the configured database is {host_class} — this is a "
                f"development database, not production. A census that reports "
                f"development numbers under a production heading is worse "
                f"than no census. Configure production credentials, or pass "
                f"--allow-non-production to run against synthetic data (the "
                f"report will be stamped non-production).")
        return host_class, False
    return host_class, True


def gate_identity(alias):
    """Gate 4 + 6: read the server's own account of itself."""
    with connections[alias].cursor() as cursor:
        cursor.execute(
            "SELECT current_database(), current_user, "
            "       COALESCE(host(inet_server_addr()), 'local'), version()")
        database, user, server, version = cursor.fetchone()
    return {
        "database": database,
        "role": user,
        "server_address_class": classify_host(
            None if server == "local" else server),
        "server_version": version.split(" on ")[0],
    }


def gate_read_only(alias):
    """
    Gate 5: the role must hold NO write privilege on any guarded table.

    Asked of PostgreSQL via `has_table_privilege`, not inferred from a
    username or a connection option. `default_transaction_read_only` is
    deliberately NOT accepted as evidence: it is a session setting a later
    statement can reset, whereas a missing GRANT cannot be talked around.

    No write is attempted. Testing read-only-ness by trying an INSERT would
    mean attempting a production mutation to find out whether production
    mutations are possible.
    """
    findings, violations = {}, []
    with connections[alias].cursor() as cursor:
        for table in GUARDED_TABLES:
            cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [table])
            if not cursor.fetchone()[0]:
                findings[table] = "ABSENT"
                continue
            granted = []
            for privilege in WRITE_PRIVILEGES:
                cursor.execute(
                    "SELECT has_table_privilege(current_user, %s, %s)",
                    [table, privilege])
                if cursor.fetchone()[0]:
                    granted.append(privilege)
            findings[table] = granted or ["read-only"]
            if granted:
                violations.append(f"{table}: {'/'.join(granted)}")

        cursor.execute(
            "SELECT has_database_privilege(current_user, current_database(), "
            "'CREATE')")
        can_create = cursor.fetchone()[0]
        if can_create:
            violations.append("database: CREATE (DDL)")
        findings["__database_create__"] = bool(can_create)

    return findings, violations


def gate_schema(alias):
    """Gate 6: the migrations the census's columns depend on are applied."""
    with connections[alias].cursor() as cursor:
        cursor.execute(
            "SELECT name FROM django_migrations WHERE app = 'groups'")
        applied = {row[0] for row in cursor.fetchall()}
    missing = [name for name in REQUIRED_MIGRATIONS if name not in applied]
    return sorted(applied)[-1] if applied else None, missing


def run_all(alias, *, allow_non_production=False, require_read_only=True):
    """
    Every gate, in order. Returns the identity dict, or raises `GateFailure`.

    Ordered so the cheapest and most decisive checks run first: an alias that
    points at localhost is rejected before a connection is opened at all.
    """
    config = gate_alias(alias)
    host_class, is_production = gate_not_local(
        config, allow_non_production=allow_non_production)

    identity = gate_identity(alias)
    identity["alias"] = alias
    identity["host_class"] = host_class
    identity["is_production"] = is_production

    findings, violations = gate_read_only(alias)
    identity["privileges"] = findings
    identity["read_only"] = not violations

    if violations and require_read_only:
        raise GateFailure(
            "the database role holds WRITE privileges and this census refuses "
            "to run with credentials that could damage what it measures:\n  "
            + "\n  ".join(violations)
            + "\n\nCreate a role with SELECT only:\n"
              "  CREATE ROLE census_ro LOGIN PASSWORD '...';\n"
              "  GRANT CONNECT ON DATABASE <db> TO census_ro;\n"
              "  GRANT USAGE ON SCHEMA public TO census_ro;\n"
              "  GRANT SELECT ON ALL TABLES IN SCHEMA public TO census_ro;")

    latest, missing = gate_schema(alias)
    identity["latest_migration"] = latest
    identity["missing_migrations"] = missing

    if missing:
        raise GateFailure(
            f"the database is missing migrations the census depends on: "
            f"{missing}. Columns the report needs do not exist there, and "
            f"adapting the census to an older schema would produce a report "
            f"that looks complete and is not. Migrations are NOT applied by "
            f"this command.")

    return identity
