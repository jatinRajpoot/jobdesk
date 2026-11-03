'use strict';
document.querySelector('#login-form').addEventListener('submit', async event => {
 event.preventDefault();
 const button = event.currentTarget.querySelector('button');
 const input = document.querySelector('#password');
 const error = document.querySelector('#login-error');
 button.disabled = true;
 error.hidden = true;
 try {
  const response = await fetch('/api/login', {
   method: 'POST', headers: {'Content-Type': 'application/json', 'x-jobdesk': '1'},
   body: JSON.stringify({password: input.value})
  });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Please enter your workspace password.');
  input.value = '';
  window.location.replace('/');
 } catch (failure) {
  error.textContent = failure instanceof TypeError ? 'Could not reach Jobdesk. Please try again.' : failure.message;
  error.hidden = false;
  input.focus();
 } finally {
  button.disabled = false;
 }
});
