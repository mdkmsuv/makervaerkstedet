import { Calculator } from './calculator.mjs';

const calculator = new Calculator();
const display = document.querySelector('#display');
const expression = document.querySelector('#expression');
const feedback = document.querySelector('#feedback');
const keypad = document.querySelector('#keypad');

function press(key) {
  const view = calculator.press(key);
  display.textContent = view.display;
  expression.textContent = view.expression || '\u00a0';
  feedback.textContent = view.status;
  feedback.classList.toggle('error', view.isError);
  for (const button of keypad.querySelectorAll('[data-operator]')) {
    button.setAttribute('aria-pressed', String(button.dataset.key === view.pendingOperator));
  }
}

keypad.addEventListener('click', event => {
  const button = event.target.closest('button[data-key]');
  if (button) press(button.dataset.key);
});

document.addEventListener('keydown', event => {
  if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
  if (event.target.closest('input, textarea, select, [contenteditable="true"]')) return;
  // Keep native Enter/Space activation when a button has keyboard focus.
  if (event.key === 'Enter' && event.target.closest('button')) return;
  const key = event.key === 'Enter' ? '='
    : event.key === 'Escape' || event.key === 'Delete' ? 'clear'
      : event.key === 'Backspace' ? 'backspace' : event.key;
  if (/^[0-9.,+*/=\-]$/.test(key) || ['clear', 'backspace'].includes(key)) {
    event.preventDefault();
    press(key);
  }
});
