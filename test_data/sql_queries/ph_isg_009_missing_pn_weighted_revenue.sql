SELECT
    SUM(CAST(f."# Value" AS REAL)) AS SourceValue
FROM facts f
JOIN Pipeline_dimension p
    ON f."%Opportunity ID" = p."%Opportunity ID"
JOIN calendar c
    ON f."%Date" = c."%Date"
WHERE
    f."Source" = 'D365 Opportunities'
    AND (
        f."Original Product ID" LIKE '%-%'
        OR f."Original Product ID" LIKE '%,%'
    )
    AND p."Forecast Category" IN ('Won', 'Commit')
    AND c."Qtr Flag" IN (0, 1)
    AND p."Opportunity Type" = 'Standard Opp'
    AND COALESCE(p."Supply Required", '') <> 'No'
    AND COALESCE(f."Business Group", '') NOT IN ('SSG', 'IDG')
    AND f."Measure" = 'Weighted Revenue USD @ Actual Rate';