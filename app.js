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

let datePicker = null; // flatpickr instance

function getServiceType() {
  const opt = serviceSelect?.options[serviceSelect.selectedIndex];
  return opt?.value || '';
}

// Sat & Tue close at 3PM (last slot 2:30PM). Mon/Wed/Thu/Fri close at 6PM (last slot 5:30PM).
function buildTimeOptions(dow) {
  const timeSelect = document.getElementById('time');
  if (!timeSelect) return;
  const prev = timeSelect.value;
  timeSelect.innerHTML = '<option value="">Select a time…</option>';

  const earlyClose = (dow === 2 || dow === 6); // Tue or Sat
  const [startH, endH, endM] = earlyClose ? [9, 14, 30] : [9, 17, 30];

  const slots = [];
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

function initDatePicker(type) {
  if (!dateInput || typeof flatpickr === 'undefined') return;

  // Destroy existing instance before reinitialising
  if (datePicker) { datePicker.destroy(); datePicker = null; }

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  let disableFn, minDate, defaultDate;

  if (type === 'haircut') {
    // Only Saturdays selectable
    disableFn = date => date.getDay() !== 6;
    // Start at next Saturday
    const next = new Date(today);
    const diff = (6 - next.getDay() + 7) % 7 || 7;
    next.setDate(next.getDate() + diff);
    minDate = next;
    defaultDate = null;
    if (dateHint) {
      dateHint.textContent = 'Haircut appointments are Saturdays only. Walk-ins welcome Monday–Friday.';
      dateHint.className = 'form-hint form-hint--info';
    }
  } else if (type === 'color') {
    // Sunday disabled (closed); all other days open
    disableFn = date => date.getDay() === 0;
    minDate = today;
    defaultDate = null;
    if (dateHint) {
      dateHint.textContent = 'Color & styling appointments with Janette are available Monday–Saturday.';
      dateHint.className = 'form-hint form-hint--info';
    }
  } else {
    // No service chosen yet — show full calendar, validate on submit
    disableFn = date => date.getDay() === 0;
    minDate = today;
    if (dateHint) { dateHint.textContent = ''; dateHint.className = 'form-hint'; }
  }

  datePicker = flatpickr(dateInput, {
    minDate,
    disable: [disableFn],
    dateFormat: 'Y-m-d',
    disableMobile: false,
    onChange(selectedDates) {
      if (!selectedDates.length) return;
      clearError('date');
      buildTimeOptions(selectedDates[0].getDay());
    },
  });

  // Clear any previously selected date when service type changes
  datePicker.clear();
  buildTimeOptions(type === 'haircut' ? 6 : 1);
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

if (serviceSelect) {
  serviceSelect.addEventListener('change', () => {
    clearError('service');
    initDatePicker(getServiceType());
  });
  // Initialise with no service selected
  initDatePicker('');
}

if (form) {
  ['first-name', 'last-name', 'phone', 'time'].forEach(id => {
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
    if (dateHint) { dateHint.textContent = ''; dateHint.className = 'form-hint'; }
    initDatePicker('');
    formSuccess.classList.add('hidden');
    form.classList.remove('hidden');
  });
}
