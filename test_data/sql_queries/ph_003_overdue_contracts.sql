WITH contract_list AS (
    SELECT DISTINCT
        CAST(f."Contract Number" AS TEXT) AS ContractNumber
    FROM facts f
    WHERE COALESCE(CAST(f."Contract Number" AS TEXT), '') <> ''
),

gross_revenue AS (
    SELECT
        cl.ContractNumber,
        SUM(CAST(l."# Gross Revenue Pricing r.USD" AS REAL)) AS GrossRevenue
    FROM contract_list cl
    JOIN facts f
        ON CAST(f."Contract Number" AS TEXT) = cl.ContractNumber
    JOIN Pipeline_dimension p
        ON CAST(p."%Opportunity ID" AS TEXT)
         = CAST(f."%Opportunity ID" AS TEXT)
    JOIN calendar c
        ON CAST(c."%Date" AS TEXT)
         = CAST(f."%Date" AS TEXT)
    JOIN Load l
        ON CAST(l."%SBO Key" AS TEXT)
         = CAST(f."%SBO Key" AS TEXT)
    WHERE
        COALESCE(CAST(l."Special Bid Number" AS TEXT), '') <> ''
        AND p."Forecast Category" = 'Won'
        AND p."Sales Stage Gate" = 'Close Won'
        AND c."Qtr Flag" IN (0, 1)
        AND COALESCE(f."Business Group Level2", '') <> 'SSG'
    GROUP BY
        cl.ContractNumber
),

revenue AS (
    SELECT
        cl.ContractNumber,
        SUM(CAST(f."# Value" AS REAL)) AS Revenue
    FROM contract_list cl
    JOIN facts f
        ON CAST(f."Contract Number" AS TEXT) = cl.ContractNumber
    JOIN Pipeline_dimension p
        ON CAST(p."%Opportunity ID" AS TEXT)
         = CAST(f."%Opportunity ID" AS TEXT)
    JOIN calendar c
        ON CAST(c."%Date" AS TEXT)
         = CAST(f."%Date" AS TEXT)
    JOIN Load l
        ON CAST(l."%SBO Key" AS TEXT)
         = CAST(f."%SBO Key" AS TEXT)
    WHERE
        COALESCE(CAST(f."Contract Number" AS TEXT), '') <> ''
        AND COALESCE(CAST(l."Special Bid Number" AS TEXT), '') <> ''
        AND p."Forecast Category" = 'Won'
        AND p."Sales Stage Gate" = 'Close Won'
        AND c."Qtr Flag" IN (0, 1)
        AND f."Measure" = 'Revenue USD @ Actual Rate'
        AND COALESCE(f."Business Group Level2", '')
            NOT IN ('SSG ISG', 'SSG IDG', 'SSG PCSD')
    GROUP BY
        cl.ContractNumber
)

SELECT
    COUNT(*) AS SourceValue
FROM contract_list cl
LEFT JOIN gross_revenue gr
    ON gr.ContractNumber = cl.ContractNumber
LEFT JOIN revenue r
    ON r.ContractNumber = cl.ContractNumber
WHERE
    COALESCE(gr.GrossRevenue, 0) - COALESCE(r.Revenue, 0) > 50000;