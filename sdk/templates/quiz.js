const form = document.querySelector('#quiz');
const feedback = document.querySelector('#feedback');
form.addEventListener('submit', event => {
  event.preventDefault();
  const answer = new FormData(form).get('answer');
  feedback.textContent = !answer ? 'Vælg et svar først.'
    : answer === 'water' ? 'Ja! Vand og lys hjælper planten med at vokse.'
      : 'Prøv igen. Tænk på, hvordan en plante får vand og lys.';
  if (!answer) form.querySelector('input').focus();
});
form.addEventListener('reset', () => {
  feedback.textContent = 'Du kan vælge et nyt svar.';
  form.querySelector('input').focus();
});
