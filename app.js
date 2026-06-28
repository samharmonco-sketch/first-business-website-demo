// Mobile nav toggle
const navToggle = document.querySelector('.nav-toggle');
const navLinks = document.querySelector('.nav-links');
if (navToggle) {
  navToggle.addEventListener('click', () => navLinks.classList.toggle('open'));
}

// Set date input min to today
const dateInput = document.getElementById('date');
if (dateInput) {
  const today = new Date().toISOString().split('T')[0];
  dateInput.min = today;
}

// Contact / booking form
const form = document.getElementById('contact-form');
const formSuccess = document.getElementById('form-success');
const resetBtn = document.getElementById('reset-form');

function showError(id, msg) {
  const err = document.getElementById(id + '-error');
  const inp = document.getElementById(id);
  if (err) err.textContent = msg;
  if (inp) inp.classList.add('error');
}
function clearError(id) {
  const err = document.getElementById(id + '-error');
  const inp = document.getElementById(id);
  if (err) err.textContent = '';
  if (inp) inp.classList.remove('error');
}

if (form) {
  ['first-name', 'last-name', 'phone', 'service', 'date', 'time'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', () => clearError(id));
    if (el) el.addEventListener('change', () => clearError(id));
  });

  form.addEventListener('submit', e => {
    e.preventDefault();
    let valid = true;

    const firstName = document.getElementById('first-name')?.value.trim();
    const lastName  = document.getElementById('last-name')?.value.trim();
    const phone     = document.getElementById('phone')?.value.trim();
    const service   = document.getElementById('service')?.value;
    const date      = document.getElementById('date')?.value;
    const time      = document.getElementById('time')?.value;

    if (!firstName) { showError('first-name', 'First name is required.'); valid = false; } else clearError('first-name');
    if (!lastName)  { showError('last-name',  'Last name is required.');  valid = false; } else clearError('last-name');
    if (!phone)     { showError('phone', 'Phone number is required.');    valid = false; } else clearError('phone');
    if (!service)   { showError('service', 'Please select a service.');   valid = false; } else clearError('service');
    if (!date)      { showError('date', 'Please choose a date.');         valid = false; } else clearError('date');
    if (!time)      { showError('time', 'Please choose a time.');         valid = false; } else clearError('time');

    if (valid) {
      form.classList.add('hidden');
      formSuccess.classList.remove('hidden');
    }
  });
}

if (resetBtn) {
  resetBtn.addEventListener('click', () => {
    form.reset();
    ['first-name', 'last-name', 'phone', 'service', 'date', 'time'].forEach(clearError);
    formSuccess.classList.add('hidden');
    form.classList.remove('hidden');
  });
}
