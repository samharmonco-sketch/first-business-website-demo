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

function dayOfWeek(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m - 1, d).getDay(); // 0=Sun,6=Sat
}
function isSaturday(dateStr) { return dayOfWeek(dateStr) === 6; }
function isSunday(dateStr)   { return dayOfWeek(dateStr) === 0; }

function nextSaturday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  const diff = (6 - d.getDay() + 7) % 7 || 7;
  d.setDate(d.getDate() + diff);
  return d.toISOString().split('T')[0];
}

// Populate time select. Saturday: 8AM open, last slot 4:30PM (closes 5PM).
// Weekdays: 9AM open, last slot 6:30PM (closes 7PM).
function buildTimeOptions(isSat) {
  const timeSelect = document.getElementById('time');
  if (!timeSelect) return;
  const prev = timeSelect.value;
  timeSelect.innerHTML = '<option value="">Select a time…</option>';

  // Each slot is [hour24, minute]
  const slots = [];
  const [startH, endH, endM] = isSat ? [8, 16, 30] : [9, 18, 30];
  for (let h = startH; h <= endH; h++) {
    for (const m of [0, 30]) {
      if (h === endH && m > endM) break;
      slots.push([h, m]);
    }
  }

  slots.forEach(([h, m]) => {
    const hour12 = h % 12 || 12;
    const ampm   = h < 12 ? 'AM' : 'PM';
    const label  = `${hour12}:${m === 0 ? '00' : '30'} ${ampm}`;
    const opt = document.createElement('option');
    opt.textContent = label;
    if (label === prev) opt.selected = true;
    timeSelect.appendChild(opt);
  });

  if (prev && timeSelect.value !== prev) timeSelect.value = '';
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
    if (dateInput?.value) clearError('date');
    // Haircuts are always Saturday, so set Saturday time slots immediately
    buildTimeOptions(getServiceType() === 'haircut');
  });
  updateDateField();
}

// Validate date and rebuild time slots when date changes
if (dateInput) {
  dateInput.addEventListener('change', () => {
    clearError('date');
    const type = getServiceType();
    const val  = dateInput.value;
    if (!val) return;
    if (isSunday(val)) {
      showError('date', 'We are closed on Sundays. Please choose another day.');
      dateInput.value = '';
      return;
    }
    if (type === 'haircut' && !isSaturday(val)) {
      showError('date', 'Please choose a Saturday for haircut appointments.');
      return;
    }
    buildTimeOptions(isSaturday(val));
  });
  // Default time slots (weekday)
  buildTimeOptions(false);
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
