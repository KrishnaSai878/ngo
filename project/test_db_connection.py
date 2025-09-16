import mysql.connector

try:
    conn = mysql.connector.connect(
        host='127.0.0.1',
        port=3306,
        user="root",
        password="sai0001sai",
        database="ngoconnect"
    )
    print("✅ Connected to external database!")
    conn.close()
except mysql.connector.Error as err:
    print("❌ Error:", err)
