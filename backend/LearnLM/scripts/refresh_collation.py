import psycopg

conn = psycopg.connect(
    dbname="postgres",
    user="postgres",
    password="yourpassword",
    host="127.0.0.1",
    port=5432,
    autocommit=True
)

conn.execute("ALTER DATABASE template1 REFRESH COLLATION VERSION;")
print("Collation refreshed")
