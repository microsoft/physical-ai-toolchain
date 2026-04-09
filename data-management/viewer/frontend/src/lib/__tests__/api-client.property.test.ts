import fc from 'fast-check'
import { describe, expect, it } from 'vitest'

import { snakeToCamel, transformKeys } from '../api-client'

const snakeChars = 'abcdefghijklmnopqrstuvwxyz_'.split('')
const alphaChars = 'abcdefghijklmnopqrstuvwxyz'.split('')

const snakeString = fc
  .array(fc.constantFrom(...snakeChars), { minLength: 1, maxLength: 50 })
  .map((chars) => chars.join(''))

const alphaString = fc
  .array(fc.constantFrom(...alphaChars), { minLength: 1, maxLength: 50 })
  .map((chars) => chars.join(''))

describe('snakeToCamel', () => {
  it('output never contains underscore followed by lowercase letter', () => {
    fc.assert(
      fc.property(snakeString, (input) => {
        const result = snakeToCamel(input)
        expect(result).not.toMatch(/_[a-z]/)
      }),
    )
  })

  it('is idempotent on its own output', () => {
    fc.assert(
      fc.property(snakeString, (input) => {
        const once = snakeToCamel(input)
        const twice = snakeToCamel(once)
        expect(twice).toBe(once)
      }),
    )
  })

  it('preserves strings without underscores', () => {
    fc.assert(
      fc.property(alphaString, (input) => {
        expect(snakeToCamel(input)).toBe(input)
      }),
    )
  })
})

describe('transformKeys', () => {
  it('preserves array length', () => {
    fc.assert(
      fc.property(
        fc.array(fc.record({ some_key: fc.integer(), another_key: fc.string() }), {
          maxLength: 20,
        }),
        (arr) => {
          const result = transformKeys(arr) as unknown[]
          expect(result).toHaveLength(arr.length)
        },
      ),
    )
  })

  it('preserves primitive values through transformation', () => {
    fc.assert(
      fc.property(fc.integer(), (num) => {
        expect(transformKeys(num)).toBe(num)
      }),
    )
  })

  it('preserves null and string primitives', () => {
    expect(transformKeys(null)).toBeNull()
    fc.assert(
      fc.property(fc.string(), (str) => {
        expect(transformKeys(str)).toBe(str)
      }),
    )
  })

  it('converts all snake_case keys in nested objects', () => {
    fc.assert(
      fc.property(fc.integer(), fc.integer(), (a, b) => {
        const input = { outer_key: { inner_key: a }, simple_key: b }
        const result = transformKeys(input) as Record<string, unknown>
        expect(result).toHaveProperty('outerKey')
        expect(result).toHaveProperty('simpleKey')
        const nested = result.outerKey as Record<string, unknown>
        expect(nested).toHaveProperty('innerKey')
        expect(nested.innerKey).toBe(a)
        expect(result.simpleKey).toBe(b)
      }),
    )
  })
})
