// Navbar scroll behavior
const navbar = document.getElementById('navbar');
if (navbar) {
  window.addEventListener('scroll', () => {
    navbar.classList.toggle('scrolled', window.scrollY > 60);
  }, { passive: true });
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
    hiddenReviews.forEach(r => r.classList.toggle('visible', expanded));
    showMoreBtn.textContent = expanded ? 'Show Fewer Reviews' : 'Show All Reviews';
  });
}

// Contact form
const form = document.getElementById('contact-form');
const formSuccess = document.getElementById('form-success');
const resetBtn = document.getElementById('reset-form');
const serviceSelect = document.getElementById('service');
const dateInput = document.getElementById('date');
const dateHint = document.getElementById('date-hint');

// Returns true if a date string (YYYY-MM-DD) falls on a Saturday
function isSaturday(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m - 1, d).getDay() === 6;
}

// Next upcoming Saturday from today
function nextSaturday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  const diff = (6 - d.getDay() + 7) % 7 || 7;
  d.setDate(d.getDate() + diff);
  return d.toISOString().split('T')[0];
}

function getServiceType() {
  const opt = serviceSelect?.options[serviceSelect.selectedIndex];
  return opt?.value || '';
}

function updateDateField() {
  if (!dateInput) return;
  const type = getServiceType();
  const today = new Date().toISOString().split('T')[0];

  if (type === 'haircut') {
    dateInput.min = nextSaturday();
    dateInput.value = '';
    if (dateHint) {
      dateHint.textContent = 'Haircut appointments are Saturdays only. Walk-ins welcome Monday–Friday.';
      dateHint.className = 'form-hint form-hint--info';
    }
  } else if (type === 'color') {
    dateInput.min = today;
    dateInput.value = '';
    if (dateHint) {
      dateHint.textContent = 'Color & styling appointments with Janette are available Monday–Saturday.';
      dateHint.className = 'form-hint form-hint--info';
    }
  } else {
    dateInput.min = today;
    dateInput.value = '';
    if (dateHint) {
      dateHint.textContent = '';
      dateHint.className = 'form-hint';
    }
  }
}

if (serviceSelect) {
  serviceSelect.addEventListener('change', () => {
    clearError('service');
    updateDateField();
    // Clear date if it no longer fits the selected service type
    if (dateInput?.value) clearError('date');
  });
  updateDateField();
}

// Validate date against service type on change
if (dateInput) {
  dateInput.addEventListener('change', () => {
    clearError('date');
    const type = getServiceType();
    if (type === 'haircut' && dateInput.value && !isSaturday(dateInput.value)) {
      showError('date', 'Please choose a Saturday for haircut appointments.');
    }
  });
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
  ['first-name', 'last-name', 'phone', 'date', 'time'].forEach(id => {
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

    // Extra check: haircut must be Saturday
    const type = getServiceType();
    if (valid && type === 'haircut' && dateInput?.value && !isSaturday(dateInput.value)) {
      showError('date', 'Please choose a Saturday for haircut appointments.');
      valid = false;
    }

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
    updateDateField();
    if (dateHint) { dateHint.textContent = ''; dateHint.className = 'form-hint'; }
    formSuccess.classList.add('hidden');
    form.classList.remove('hidden');
  });
}
