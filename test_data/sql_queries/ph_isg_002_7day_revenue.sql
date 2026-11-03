SELECT
    SUM(CAST(f."# Value" AS REAL)) AS SourceValue
FROM facts f
JOIN Pipeline_dimension p
    ON CAST(p."%Opportunity ID" AS TEXT)
     = CAST(f."%Opportunity ID" AS TEXT)
JOIN calendar c
    ON CAST(c."%Date" AS TEXT)
     = CAST(f."%Date" AS TEXT)
JOIN Country co
    ON CAST(co."%Country ID" AS TEXT)
     = CAST(f."%Country ID" AS TEXT)
WHERE f."Measure" = 'Revenue USD @ Actual Rate'
  AND CAST(p."Flag Open" AS INTEGER) = -1
  AND CAST(p."Days To Close" AS INTEGER) BETWEEN 0 AND 7
  AND c."Calendar YearMonth" = '2026-09'
  AND co."GEO Level3" IN ('CZ/SK', 'EECA');