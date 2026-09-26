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
