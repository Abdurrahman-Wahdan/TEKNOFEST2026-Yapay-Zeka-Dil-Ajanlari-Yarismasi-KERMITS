# Agent reasoning rules

## Natural-language financial intent

The supervisor and bank-specialist LLMs interpret financial intent from the
user's own language. For a financing tool call, a bank that supports a
customer-selected monthly profit rate requires the explicit
`monthly_profit_rate` field: the LLM supplies the requested value or `null`
for the bank's live rate.

No regexes, keyword matching, or text-pattern heuristics may extract or
classify financial intent in the agent hand-off path. Deterministic validation
starts after the live call: a customer scenario rate must match the rate the
calculator returned, or the response is refused.
