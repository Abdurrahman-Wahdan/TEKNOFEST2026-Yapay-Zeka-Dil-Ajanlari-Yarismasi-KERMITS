"""The request and response shapes -- and, through OpenAPI, the frontend's types.

These models are the contract. `UI/` does not hand-write interfaces for anything
in here; `npm run api:types` reads this service's OpenAPI schema and generates
them, so a field renamed in Python becomes a TypeScript compile error rather
than an `undefined` discovered by a user.

Two conventions that keep the generated types clean:

- Every response model is explicit. No endpoint returns a bare dict, because a
  dict generates as `Record<string, unknown>` and erases the contract.
- Optional means optional, not "sometimes missing". A field that may have no
  value is typed `X | None` and is always present in the JSON.
"""

__all__ = []
