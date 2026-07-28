import pandas as pd
import os

test_cases = [
    {
        "Test ID": "TC-BIZ-001",
        "Scenario Name": "State-Level Sales Validation",
        "Slicer 1 Name": "State",
        "Slicer 1 Value": "California",
        "Slicer 2 Name": None,
        "Slicer 2 Value": None,
        "KPI to Read": "Total Sales",
        "SQL File Name": "tc001.sql"
    },
    {
        "Test ID": "TC-BIZ-002",
        "Scenario Name": "Segment Isolation on Pie Chart",
        "Slicer 1 Name": "Customer Segment",
        "Slicer 1 Value": "Corporate",
        "Slicer 2 Name": None,
        "Slicer 2 Value": None,
        "KPI to Read": "Sales by Customer",
        "SQL File Name": "tc002.sql"
    },
    {
        "Test ID": "TC-BIZ-003",
        "Scenario Name": "Multi-Condition Chart Filtering",
        "Slicer 1 Name": "Category",
        "Slicer 1 Value": "Office Supplies",
        "Slicer 2 Name": "Ship Mode",
        "Slicer 2 Value": "Standard Class",
        "KPI to Read": "Total Sales by City",
        "SQL File Name": "tc003.sql"
    },
    {
        "Test ID": "TC-BIZ-004",
        "Scenario Name": "Yearly Data Restriction",
        "Slicer 1 Name": "Order Date",
        "Slicer 1 Value": "2018",
        "Slicer 2 Name": None,
        "Slicer 2 Value": None,
        "KPI to Read": "Total Sales",
        "SQL File Name": "tc004.sql"
    },
    {
        "Test ID": "TC-BIZ-005",
        "Scenario Name": "Dynamic Coordinate Tracking",
        "Slicer 1 Name": "Sub-Category",
        "Slicer 1 Value": "Phones",
        "Slicer 2 Name": None,
        "Slicer 2 Value": None,
        "KPI to Read": "Total Profit",
        "SQL File Name": "tc005.sql"
    },
    {
        "Test ID": "TC-BIZ-006",
        "Scenario Name": "Trend Line Peak Verification",
        "Slicer 1 Name": "City",
        "Slicer 1 Value": "New York City",
        "Slicer 2 Name": None,
        "Slicer 2 Value": None,
        "KPI to Read": "Sales Trend",
        "SQL File Name": "tc006.sql"
    },
    {
        "Test ID": "TC-BIZ-007",
        "Scenario Name": "Geographic Map Updates",
        "Slicer 1 Name": "Customer Segment",
        "Slicer 1 Value": "Home Office",
        "Slicer 2 Name": "State",
        "Slicer 2 Value": "Florida",
        "KPI to Read": "Total Quantity",
        "SQL File Name": "tc007.sql"
    },
    {
        "Test ID": "TC-BIZ-008",
        "Scenario Name": "Filter Reset Behavior",
        "Slicer 1 Name": "Category",
        "Slicer 1 Value": "Furniture",
        "Slicer 2 Name": None,
        "Slicer 2 Value": None,
        "KPI to Read": "Total Quantity",
        "SQL File Name": "tc008.sql"
    },
    {
        "Test ID": "TC-BIZ-009",
        "Scenario Name": "Single-Day Metric Accuracy",
        "Slicer 1 Name": "Order Date",
        "Slicer 1 Value": "5/13/2016",
        "Slicer 2 Name": None,
        "Slicer 2 Value": None,
        "KPI to Read": "# of Orders",
        "SQL File Name": "tc009.sql"
    },
    {
        "Test ID": "TC-BIZ-010",
        "Scenario Name": "Cross-Page Total Matching",
        "Slicer 1 Name": "Category",
        "Slicer 1 Value": "Technology",
        "Slicer 2 Name": "Ship Mode",
        "Slicer 2 Value": "First Class",
        "KPI to Read": "Total Sales",
        "SQL File Name": "tc010.sql"
    },
    {
        "Test ID": "TC-BIZ-011",
        "Scenario Name": "Negative Profit Formatting",
        "Slicer 1 Name": "City",
        "Slicer 1 Value": "Philadelphia", # Example of typically low profit city
        "Slicer 2 Name": "Category",
        "Slicer 2 Value": "Furniture",
        "KPI to Read": "Total Profit",
        "SQL File Name": "tc011.sql"
    },
    {
        "Test ID": "TC-BIZ-012",
        "Scenario Name": "Hierarchy Override",
        "Slicer 1 Name": "City",
        "Slicer 1 Value": "Los Angeles",
        "Slicer 2 Name": "State",
        "Slicer 2 Value": "All",
        "KPI to Read": "Total Sales",
        "SQL File Name": "tc012.sql"
    },
    {
        "Test ID": "TC-BIZ-013",
        "Scenario Name": "Intersecting Data Counts",
        "Slicer 1 Name": "Ship Mode",
        "Slicer 1 Value": "Same Day",
        "Slicer 2 Name": "Customer Segment",
        "Slicer 2 Value": "Consumer",
        "KPI to Read": "# of Products",
        "SQL File Name": "tc013.sql"
    },
    {
        "Test ID": "TC-BIZ-014",
        "Scenario Name": "Time Axis Scaling",
        "Slicer 1 Name": "Order Date",
        "Slicer 1 Value": "2017",
        "Slicer 2 Name": None,
        "Slicer 2 Value": None,
        "KPI to Read": "Sales Trend",
        "SQL File Name": "tc014.sql"
    },
    {
        "Test ID": "TC-BIZ-015",
        "Scenario Name": "Zero-State Handling",
        "Slicer 1 Name": "City",
        "Slicer 1 Value": "Los Angeles",
        "Slicer 2 Name": "State",
        "Slicer 2 Value": "Texas", # Contradictory filters
        "KPI to Read": "Total Sales",
        "SQL File Name": "tc015.sql"
    }
]

df = pd.DataFrame(test_cases)
df.to_excel("test_data/business_scenarios.xlsx", index=False)
print("Created business_scenarios.xlsx")

# Create mock SQL files
os.makedirs("test_data/sql_queries", exist_ok=True)
for i in range(1, 16):
    sql_file = f"test_data/sql_queries/tc{i:03d}.sql"
    with open(sql_file, "w") as f:
        f.write(f"-- Mock SQL Query for TC-BIZ-{i:03d}\\n")
        f.write(f"SELECT SUM([Sales]) FROM [SALES] -- Placeholder for {sql_file}\\n")
print("Created 15 mock SQL files")
