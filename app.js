// Navbar scroll behavior
const navbar = document.getElementById('navbar');
if (navbar) {
  window.addEventListener('scroll', () => {
    navbar.classList.toggle('scrolled', window.scrollY > 60);
  }, { passive: true });
}

// Hero image zoom on load
const heroBg = document.querySelector('.hero-bg');
if (heroBg) {
  const img = new Image();
  img.onload = () => heroBg.classList.add('loaded');
  img.src = heroBg.style.backgroundImage.replace(/url\(['"]?(.*?)['"]?\)/, '$1');
}

// Mobile nav toggle
const navToggle = document.querySelector('.nav-toggle');
const navLinks = document.querySelector('.nav-links');
if (navToggle) {
  navToggle.addEventListener('click', () => navLinks.classList.toggle('open'));
  document.addEventListener('click', e => {
    if (!navbar.contains(e.target)) navLinks.classList.remove('open');
  });
}

// Show more / fewer reviews
const showMoreBtn = document.getElementById('show-more-reviews');
const hiddenReviews = document.querySelectorAll('.hidden-review');
let expanded = false;
if (showMoreBtn) {
  showMoreBtn.addEventListener('click', () => {
    expanded = !expanded;
    hiddenReviews.forEach(r => {
      r.classList.toggle('visible', expanded);
    });
    showMoreBtn.textContent = expanded ? 'Show Fewer Reviews' : 'Show All Reviews';
  });
}

// Contact form
const form = document.getElementById('contact-form');
const formSuccess = document.getElementById('form-success');
const resetBtn = document.getElementById('reset-form');

const dateInput = document.getElementById('date');
if (dateInput) {
  dateInput.min = new Date().toISOString().split('T')[0];
}

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
    if (el) {
      el.addEventListener('input', () => clearError(id));
      el.addEventListener('change', () => clearError(id));
    }
  });

  form.addEventListener('submit', e => {
    e.preventDefault();
    let valid = true;

    const checks = [
      ['first-name', 'First name is required.'],
      ['last-name',  'Last name is required.'],
      ['phone',      'Phone number is required.'],
      ['service',    'Please select a service.'],
      ['date',       'Please choose a date.'],
      ['time',       'Please choose a time.'],
    ];

    checks.forEach(([id, msg]) => {
      const val = document.getElementById(id)?.value?.trim();
      if (!val) { showError(id, msg); valid = false; }
      else clearError(id);
    });

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
