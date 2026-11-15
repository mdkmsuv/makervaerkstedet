// All working values live only in this instance; no browser storage.
const signs = { '+': '+', '-': '−', '*': '×', '/': '÷' };
const pretty = value => String(value).replace('.', ',').replaceAll('-', '−');
const rounded = number => Number(number.toPrecision(12));

export class Calculator {
  constructor() { this.reset(); }
  reset() {
    this.current = '0';
    this.left = null;
    this.operator = null;
    this.replace = false;
    this.finished = false;
    this.expression = '';
    this.status = '';
    this.error = null;
  }
  view() {
    return {
      display: pretty(this.current),
      expression: this.operator ? `${pretty(this.left)} ${signs[this.operator]}` : this.expression,
      status: this.status,
      isError: this.error !== null,
      pendingOperator: this.operator,
    };
  }
  solve() {
    const right = Number(this.current);
    if (this.operator === '/' && right === 0) {
      this.status = 'Du kan ikke dividere med 0. Skriv et andet tal, og tryk =.';
      this.error = 'zero';
      this.replace = true;
      return false;
    }
    const result = this.operator === '+' ? this.left + right
      : this.operator === '-' ? this.left - right
        : this.operator === '*' ? this.left * right : this.left / right;
    if (!Number.isFinite(result)) {
      this.status = 'Tallet er for stort. Tryk Ryd, og prøv med mindre tal.';
      this.error = 'overflow';
      return false;
    }
    this.expression = `${pretty(this.left)} ${signs[this.operator]} ${pretty(right)} =`;
    this.current = String(rounded(result));
    this.left = null;
    this.operator = null;
    this.replace = true;
    this.finished = true;
    return true;
  }
  press(key) {
    if (key === 'clear') { this.reset(); return this.view(); }
    if (this.error === 'overflow') return this.view();
    if (/^\d$/.test(key) || key === ',' || key === '.') {
      if (this.finished) this.reset();
      if (this.replace) { this.current = '0'; this.replace = false; }
      this.error = null;
      this.status = '';
      if (key === ',' || key === '.') {
        if (!this.current.includes('.')) this.current += '.';
      } else if (this.current.replace(/[^0-9]/g, '').length >= 12) {
        this.status = 'Du kan skrive op til 12 cifre. Slet et ciffer, eller tryk Ryd.';
      } else {
        this.current = this.current === '0' ? key
          : this.current === '-0' ? '-' + key : this.current + key;
      }
    } else if (key === 'backspace') {
      if (this.finished) this.reset();
      else if (!this.replace || this.error === 'zero') {
        this.current = this.current.slice(0, -1);
        if (!this.current || this.current === '-') this.current = '0';
        this.replace = false;
        this.error = null;
        this.status = '';
      }
    } else if (key === 'sign') {
      if (this.error) return this.view();
      if (this.replace && !this.finished) this.current = '0';
      this.current = this.current.startsWith('-') ? this.current.slice(1) : '-' + this.current;
      if (this.finished) this.expression = '';
      // Changing the sign of a result keeps it a result: the next digit
      // starts a new entry, including when the result uses exponent notation.
      if (!this.finished) this.replace = false;
      this.status = '';
    } else if (key in signs) {
      if (this.error) return this.view();
      if (this.operator && !this.replace && !this.solve()) return this.view();
      this.left = Number(this.current);
      this.operator = key;
      this.replace = true;
      this.finished = false;
      this.expression = '';
      this.status = 'Skriv det næste tal, og tryk =.';
    } else if (key === '=' && this.operator && !this.error) {
      if (this.replace) this.status = 'Skriv det næste tal, og tryk =.';
      else if (this.solve()) this.status = '';
    }
    return this.view();
  }
}
