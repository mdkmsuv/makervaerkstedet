import test from 'node:test';
import assert from 'node:assert/strict';
import { Calculator } from '../web/calculator.mjs';

function calculate(...keys) {
  const calculator = new Calculator();
  for (const key of keys.join(' ' ).split(' ')) calculator.press(key);
  return calculator;
}

test('de fire regnearter og negative resultater', () => {
  for (const [keys, expected] of [
    ['8 + 3 =', '11'], ['3 - 8 =', '−5'], ['8 * 3 =', '24'], ['8 / 2 =', '4'],
  ]) assert.equal(calculate(keys).view().display, expected);
});

test('decimalkomma og punktum giver samme tal uden synlig flydende afrundingsstøj', () => {
  assert.equal(calculate('0 , 1 + 0 . 2 =').view().display, '0,3');
  assert.equal(calculate(', 5 * 2 =').view().display, '1');
  assert.equal(calculate('1 , , 2').view().display, '1,2');
});

test('kædning beregner ét trin ad gangen og en ny regneart erstatter den ventende', () => {
  assert.equal(calculate('2 + 3 * 4 =').view().display, '20');
  assert.equal(calculate('8 + * 2 =').view().display, '16');
  assert.equal(calculate('8 + =').view().display, '8');
});

test('division med nul kan rettes uden at miste første tal', () => {
  const calculator = calculate('8 / 0 =');
  assert.match(calculator.view().status, /dividere med 0/);
  assert.equal(calculator.view().isError, true);
  calculator.press('2');
  calculator.press('=');
  assert.equal(calculator.view().display, '4');
  assert.equal(calculator.view().isError, false);
});

test('fortegn, slet sidste tegn, ryd og nyt regnestykke efter resultat', () => {
  assert.equal(calculate('1 2 backspace sign + 2 =').view().display, '1');
  assert.equal(calculate('9 + 2 clear 3 =').view().display, '3');
  assert.equal(calculate('8 / 2 = 7').view().display, '7');
  assert.equal(calculate('8 / 2 = + 3 =').view().display, '7');
  assert.equal(calculate('8 / 2 = =').view().display, '4');
  assert.equal(calculate('1 / 3 = sign 2').view().display, '2');
  assert.equal(calculate('8 + sign 2 =').view().display, '6');
});

test('inputgrænse forklares, store og små resultater forbliver endelige', () => {
  const calculator = calculate('1 2 3 4 5 6 7 8 9 0 1 2 3');
  assert.equal(calculator.view().display, '123456789012');
  assert.match(calculator.view().status, /12 cifre/);
  assert.equal(calculate('1 / 3 =').view().display, '0,333333333333');
  assert.equal(calculate('1 / 1 0 0 0 0 0 0 0 0 0 0 0 =').view().display, '1e−11');
  const large = calculate('9 9 9 9 9 9 9 9 9 9 9 9');
  for (let count = 0; count < 30; count++) {
    large.press('*');
    for (let digit = 0; digit < 12; digit++) large.press('9');
    large.press('=');
  }
  assert.equal(large.view().isError, true);
  assert.match(large.view().status, /for stort/);
  large.press('clear');
  assert.equal(large.view().display, '0');
});
