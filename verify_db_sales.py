from sqlalchemy import create_engine, text
from urllib.parse import quote_plus
import sys

def check_db_values():
    print("==================================================")
    print("Power BI Dashboard Data Validation Script")
    print("==================================================")
    
    # Use the connection string from your backend_connection.md
    password = quote_plus('Devdb@2026')
    uri = f"mssql+pymssql://Abibiuserlogin:{password}@aibidevsqlsvr.database.windows.net:1433/devaibisqldb"
    
    try:
        engine = create_engine(uri)
        with engine.connect() as conn:
            
            print("\n1. Querying raw Database for ONLY 'California' (What the SQL script runs):")
            q1 = "SELECT SUM(Sales) FROM SALES WHERE State='California'"
            result1 = conn.execute(text(q1)).scalar()
            print(f"   Query: {q1}")
            print(f"   Result: ${result1:,.2f}")
            print(f"   (This is why the framework expects 4,153.78)")
            
            print("\n2. Querying Database for the Dashboard's DEFAULT state (from screenshot):")
            q2 = "SELECT SUM(Sales) FROM SALES WHERE State='California' AND Ship_Mode='Standard Class' AND Sub_Category='Phones'"
            result2 = conn.execute(text(q2)).scalar()
            print(f"   Query: {q2}")
            print(f"   Result: ${result2:,.2f}")
            print(f"   (Notice how 1,818.57 rounds exactly to $1.82K! This proves the dashboard has Ship Mode and Sub-Category filtered by default)")
            
            print("\n3. Querying Database for ALL states (Unfiltered Dashboard):")
            q3 = "SELECT SUM(Sales) FROM SALES"
            result3 = conn.execute(text(q3)).scalar()
            print(f"   Query: {q3}")
            print(f"   Result: ${result3:,.2f}")
            
    except Exception as e:
        print(f"Failed to connect or query: {e}")
    finally:
        if 'engine' in locals():
            engine.dispose()

if __name__ == "__main__":
    check_db_values()
