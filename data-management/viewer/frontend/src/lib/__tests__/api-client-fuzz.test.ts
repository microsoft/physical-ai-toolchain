/**
 * Fuzz harness for API client key-transformation utilities.
 *
 * Complements api-client.property.test.ts with adversarial inputs:
 * full Unicode, control characters, deeply nested structures, and
 * boundary values that exercise crash-resistance and invariant
 * preservation under arbitrary data.
 */
import fc from 'fast-check'
import { describe, expect, it } from 'vitest'

import { snakeToCamel, transformKeys } from '../api-client'

/** Build a JSON-like arbitrary at the given nesting depth. */
function jsonLike(maxDepth: number): fc.Arbitrary<unknown> {
  const leaf = fc.oneof(
    fc.integer(),
    fc.double({ noNaN: true }),
    fc.string(),
    fc.boolean(),
    fc.constant(null),
  )
  let current: fc.Arbitrary<unknown> = leaf
  for (let d = 0; d < maxDepth; d++) {
    const inner = current
    current = fc.oneof(
      { weight: 3, arbitrary: leaf },
      { weight: 1, arbitrary: fc.array(inner, { maxLength: 5 }) },
      { weight: 1, arbitrary: fc.dictionary(fc.string(), inner, { maxKeys: 5 }) },
    )
  }
  return current
}

/** Arbitrary producing strings from the full 16-bit code-point range. */
const anyChars = fc
  .array(fc.integer({ min: 0, max: 0xffff }), { minLength: 0, maxLength: 60 })
  .map((codes) => String.fromCharCode(...codes))

describe('snakeToCamel fuzz', () => {
  it('never throws on arbitrary Unicode input', () => {
    fc.assert(
      fc.property(anyChars, (input) => {
        expect(() => snakeToCamel(input)).not.toThrow()
      }),
    )
  })

  it('never throws on 16-bit strings including control characters', () => {
    fc.assert(
      fc.property(anyChars, (input) => {
        expect(() => snakeToCamel(input)).not.toThrow()
      }),
    )
  })

  it('output length is at most input length', () => {
    fc.assert(
      fc.property(anyChars, (input) => {
        expect(snakeToCamel(input).length).toBeLessThanOrEqual(input.length)
      }),
    )
  })

  it('is idempotent on arbitrary Unicode strings', () => {
    fc.assert(
      fc.property(anyChars, (input) => {
        const once = snakeToCamel(input)
        expect(snakeToCamel(once)).toBe(once)
      }),
    )
  })

  it('output never contains underscore-lowercase pattern on arbitrary input', () => {
    fc.assert(
      fc.property(anyChars, (input) => {
        expect(snakeToCamel(input)).not.toMatch(/_[a-z]/)
      }),
    )
  })

  it('returns empty string for empty input', () => {
    expect(snakeToCamel('')).toBe('')
  })

  it('handles strings composed entirely of underscores', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 50 }).map((n) => '_'.repeat(n)),
        (input) => {
          const result = snakeToCamel(input)
          expect(result).toBe(input)
          expect(result).not.toMatch(/_[a-z]/)
        },
      ),
    )
  })

  it('handles strings with embedded null bytes', () => {
    fc.assert(
      fc.property(fc.string(), (base) => {
        const withNull = `${base}\0${base}`
        expect(() => snakeToCamel(withNull)).not.toThrow()
      }),
    )
  })
})

describe('transformKeys fuzz', () => {
  it('never throws on arbitrary JSON-like input', () => {
    fc.assert(
      fc.property(jsonLike(4), (input) => {
        expect(() => transformKeys(input)).not.toThrow()
      }),
    )
  })

  it('preserves primitive values unchanged', () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.integer(),
          fc.double({ noNaN: true }),
          fc.string(),
          fc.boolean(),
          fc.constant(null),
        ),
        (input) => {
          expect(transformKeys(input)).toBe(input)
        },
      ),
    )
  })

  it('undefined passes through as a primitive', () => {
    expect(transformKeys(undefined)).toBeUndefined()
  })

  it('preserves array length on arbitrary arrays', () => {
    fc.assert(
      fc.property(fc.array(jsonLike(2), { maxLength: 20 }), (arr) => {
        const result = transformKeys(arr) as unknown[]
        expect(result).toHaveLength(arr.length)
      }),
    )
  })

  it('output key count does not exceed input key count', () => {
    fc.assert(
      fc.property(fc.dictionary(fc.string(), jsonLike(1), { maxKeys: 10 }), (obj) => {
        const result = transformKeys(obj) as Record<string, unknown>
        expect(Object.keys(result).length).toBeLessThanOrEqual(Object.keys(obj).length)
      }),
    )
  })

  it('handles deeply nested structures without throwing', () => {
    fc.assert(
      fc.property(jsonLike(8), (input) => {
        expect(() => transformKeys(input)).not.toThrow()
      }),
      { numRuns: 50 },
    )
  })

  it('preserves leaf values through nested transformation', () => {
    fc.assert(
      fc.property(fc.integer(), fc.string(), fc.boolean(), (num, str, bool) => {
        const input = {
          num_value: num,
          str_value: str,
          nested_obj: { bool_value: bool },
        }
        const result = transformKeys<{
          numValue: number
          strValue: string
          nestedObj: { boolValue: boolean }
        }>(input)
        expect(result.numValue).toBe(num)
        expect(result.strValue).toBe(str)
        expect(result.nestedObj.boolValue).toBe(bool)
      }),
    )
  })

  it('arrays of objects maintain element order', () => {
    fc.assert(
      fc.property(
        fc
          .array(fc.integer(), { minLength: 1, maxLength: 10 })
          // cspell:ignore nums
          .map((nums) => nums.map((n, i) => ({ item_index: i, item_value: n }))),
        (arr) => {
          const result = transformKeys(arr) as Array<{ itemIndex: number; itemValue: number }>
          result.forEach((item, i) => {
            expect(item.itemIndex).toBe(i)
            expect(item.itemValue).toBe(arr[i].item_value)
          })
        },
      ),
    )
  })

  it('handles mixed arrays of primitives and objects', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.oneof(
            fc.integer(),
            fc.string(),
            fc.constant(null),
            fc.record({ a_key: fc.integer() }),
          ),
          { maxLength: 10 },
        ),
        (arr) => {
          const result = transformKeys(arr) as unknown[]
          expect(result).toHaveLength(arr.length)
        },
      ),
    )
  })

  it('all output keys satisfy snake-to-camel invariant', () => {
    fc.assert(
      fc.property(fc.dictionary(fc.string(), fc.integer(), { maxKeys: 10 }), (obj) => {
        const result = transformKeys(obj) as Record<string, unknown>
        for (const key of Object.keys(result)) {
          expect(key).not.toMatch(/_[a-z]/)
        }
      }),
    )
  })
})
