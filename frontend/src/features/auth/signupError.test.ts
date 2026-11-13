import { expect, test } from 'vitest'
import { signupErrorKey } from './signupError'

test('409 is err_taken', () => expect(signupErrorKey(409, undefined)).toBe('err_taken'))
test('403 is err_limit', () => expect(signupErrorKey(403, 'x')).toBe('err_limit'))
test('reserved username error', () =>
  expect(signupErrorKey(422, [{ loc: ['body', 'username'], msg: 'Value error, name is reserved' }])).toBe('err_reserved'))
test('other username error is the hint', () =>
  expect(signupErrorKey(422, [{ loc: ['body', 'username'], msg: 'too short' }])).toBe('usernameHint'))
test('password error is the hint', () =>
  expect(signupErrorKey(422, [{ loc: ['body', 'password'], msg: 'too short' }])).toBe('passwordHint'))
test('other field uses the message', () =>
  expect(signupErrorKey(422, [{ loc: ['body', 'name'], msg: 'bad name' }])).toBe('bad name'))
test('array without message gives Error', () => expect(signupErrorKey(422, [{}])).toBe('Error'))
test('string detail is returned', () => expect(signupErrorKey(400, 'nope')).toBe('nope'))
test('no detail gives Error', () => expect(signupErrorKey(500, undefined)).toBe('Error'))
