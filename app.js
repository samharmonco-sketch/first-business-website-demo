// Mobile nav toggle
const navToggle = document.querySelector('.nav-toggle');
const navLinks = document.querySelector('.nav-links');
if (navToggle) {
  navToggle.addEventListener('click', () => navLinks.classList.toggle('open'));
}

// Menu category filter
const filterBtns = document.querySelectorAll('.filter-btn');
const menuSections = document.querySelectorAll('.menu-section');

filterBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    filterBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    const filter = btn.dataset.filter;
    menuSections.forEach(section => {
      if (filter === 'all' || section.dataset.category === filter) {
        section.classList.remove('hidden');
      } else {
        section.classList.add('hidden');
      }
    });
  });
});

// Contact form validation
const form = document.getElementById('contact-form');
const formSuccess = document.getElementById('form-success');
const resetBtn = document.getElementById('reset-form');

function showError(fieldId, message) {
  const el = document.getElementById(fieldId + '-error');
  const input = document.getElementById(fieldId);
  if (el) el.textContent = message;
  if (input) input.classList.add('error');
}

function clearError(fieldId) {
  const el = document.getElementById(fieldId + '-error');
  const input = document.getElementById(fieldId);
  if (el) el.textContent = '';
  if (input) input.classList.remove('error');
}

function validateEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

if (form) {
  // Live clearing of errors on input
  ['first-name', 'last-name', 'email', 'subject', 'message'].forEach(id => {
    const input = document.getElementById(id);
    if (input) input.addEventListener('input', () => clearError(id));
  });

  form.addEventListener('submit', e => {
    e.preventDefault();
    let valid = true;

    const firstName = document.getElementById('first-name').value.trim();
    const lastName  = document.getElementById('last-name').value.trim();
    const email     = document.getElementById('email').value.trim();
    const subject   = document.getElementById('subject').value;
    const message   = document.getElementById('message').value.trim();

    if (!firstName) { showError('first-name', 'First name is required.'); valid = false; }
    else clearError('first-name');

    if (!lastName) { showError('last-name', 'Last name is required.'); valid = false; }
    else clearError('last-name');

    if (!email) { showError('email', 'Email address is required.'); valid = false; }
    else if (!validateEmail(email)) { showError('email', 'Please enter a valid email address.'); valid = false; }
    else clearError('email');

    if (!subject) { showError('subject', 'Please select a subject.'); valid = false; }
    else clearError('subject');

    if (!message) { showError('message', 'Please enter a message.'); valid = false; }
    else if (message.length < 10) { showError('message', 'Message must be at least 10 characters.'); valid = false; }
    else clearError('message');

    if (valid) {
      form.classList.add('hidden');
      formSuccess.classList.remove('hidden');
    }
  });
}

if (resetBtn) {
  resetBtn.addEventListener('click', () => {
    form.reset();
    ['first-name', 'last-name', 'email', 'subject', 'message'].forEach(id => clearError(id));
    formSuccess.classList.add('hidden');
    form.classList.remove('hidden');
  });
}
