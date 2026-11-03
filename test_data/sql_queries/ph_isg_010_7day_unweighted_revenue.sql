SELECT
    SUM(CAST(f."# Value" AS REAL)) AS SourceValue
FROM facts f
JOIN Pipeline_dimension p
    ON f."%Opportunity ID" = p."%Opportunity ID"
JOIN calendar c
    ON f."%Date" = c."%Date"
WHERE
    f."Source" = 'D365 Opportunities'
    AND p."Forecast Category" = 'Won'
    AND p."Sales Stage Gate" = 'Close Won'
    AND CAST(p."Opportunity Closed Date - Opportunity Created Date" AS INTEGER) IN (0, 1, 2, 3, 4, 5, 6, 7)
    AND c."Qtr Flag" IN (0, 1)
    AND f."Business Group" = 'IDG'
    AND f."Measure" = 'Revenue USD @ Actual Rate';