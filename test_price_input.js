const assert = require('node:assert/strict');
const { formatPriceInput } = require('./static/price-input.js');
assert.equal(formatPriceInput('1500000'), '1,500,000');
assert.equal(formatPriceInput('2500.00').replace(/\.00$/, ''), '2,500');
assert.equal(formatPriceInput('2500.50'), '2,500.50');
assert.equal(formatPriceInput('₦2,500.5x'), '2,500.5');
assert.equal(formatPriceInput('12.345'), '12.34');
assert.equal(formatPriceInput('1.2.3'), '1.23');
console.log('ALL PRICE INPUT CHECKS PASSED');
