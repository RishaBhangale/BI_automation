SELECT
    SUM(CAST(f."# Value" AS REAL)) AS SourceValue
FROM facts f
JOIN Pipeline_dimension p
    ON f."%Opportunity ID" = p."%Opportunity ID"
JOIN calendar c
    ON f."%Date" = c."%Date"
WHERE
    f."Source" = 'D365 Opportunities'
    AND COALESCE(p."Forecast Category", '') NOT IN ('Omit', 'Won', '')
    AND COALESCE(p."Sales Stage Gate", '') NOT IN ('Close Lost', 'Close Won')
    AND (
        CAST(
            SUBSTR(
                f."Estimated Close Date",
                INSTR(f."Estimated Close Date", '/') +
                INSTR(
                    SUBSTR(
                        f."Estimated Close Date",
                        INSTR(f."Estimated Close Date", '/') + 1
                    ),
                    '/'
                ) + 1,
                4
            ) AS INTEGER
        ) * 10000
        +
        CAST(
            SUBSTR(
                f."Estimated Close Date",
                1,
                INSTR(f."Estimated Close Date", '/') - 1
            ) AS INTEGER
        ) * 100
        +
        CAST(
            SUBSTR(
                f."Estimated Close Date",
                INSTR(f."Estimated Close Date", '/') + 1,
                INSTR(
                    SUBSTR(
                        f."Estimated Close Date",
                        INSTR(f."Estimated Close Date", '/') + 1
                    ),
                    '/'
                ) - 1
            ) AS INTEGER
        )
    ) < 20260924
    AND c."Qtr Flag" IN (0, 1)
    AND f."Measure" = 'Revenue USD @ Actual Rate'
    AND COALESCE(f."Business Group", '') NOT IN ('SSG', 'IDG');