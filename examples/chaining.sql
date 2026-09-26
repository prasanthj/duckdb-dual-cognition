-- After LOAD and CREATE SECRET TYPE dc.
WITH classified AS MATERIALIZED (
  SELECT id, evidence,
         (system_one_choice(evidence, 'Choose route',
           '{"billing":"payments","technical":"product failures"}'::JSON)).choice AS route
  FROM incoming
), drafted AS MATERIALIZED (
  SELECT id, route,
         (system_two_generate({'route':route,'evidence':evidence},
           'Draft a concise next action')).value AS action
  FROM classified
)
SELECT id, route, action,
       (system_one_noul(action, 'Is this safe to send?')).noul AS safety
FROM drafted;

-- Escalate only ambiguous classifications and keep one output column.
WITH params(confidence_threshold) AS (VALUES (0.80)),
fast AS MATERIALIZED (
  SELECT id, evidence,
         system_one_choice(evidence, 'Classify the case',
           '{"billing":"payments","technical":"product failures",'
           '"security":"access and data risk"}'::JSON) AS result
  FROM incoming
), escalated AS MATERIALIZED (
  SELECT id, result.choice AS fast_choice, result.confidence, confidence_threshold,
         CASE WHEN result.confidence < confidence_threshold THEN
           (system_two_generate(
             {'evidence':evidence,'system_one_candidate':result.choice},
             'Return exactly one label: billing, technical, or security'
           )).value
         END AS reasoned_choice
  FROM fast CROSS JOIN params
)
SELECT id,
       CASE
         WHEN confidence >= confidence_threshold THEN fast_choice
         WHEN reasoned_choice IN ('billing','technical','security') THEN reasoned_choice
         ELSE 'manual_review'
       END AS classification
FROM escalated;
