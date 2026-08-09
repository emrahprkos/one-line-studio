const screenshotInput = document.querySelector('#screenshotInput');
const previewWrap = document.querySelector('#previewWrap');
const previewImage = document.querySelector('#previewImage');
const changeButton = document.querySelector('#changeButton');
const solveButton = document.querySelector('#solveButton');
const solveButtonText = document.querySelector('#solveButtonText');
const spinner = document.querySelector('#spinner');
const matrixInput = document.querySelector('#matrixInput');
const startInput = document.querySelector('#startInput');
const statusCard = document.querySelector('#statusCard');
const statusIcon = document.querySelector('#statusIcon');
const statusTitle = document.querySelector('#statusTitle');
const statusText = document.querySelector('#statusText');
const resultCard = document.querySelector('#resultCard');
const resultTitle = document.querySelector('#resultTitle');
const confidencePill = document.querySelector('#confidencePill');
const solutionImage = document.querySelector('#solutionImage');
const metrics = document.querySelector('#metrics');
const routeText = document.querySelector('#routeText');
const asciiRoute = document.querySelector('#asciiRoute');
const shareButton = document.querySelector('#shareButton');
const openButton = document.querySelector('#openButton');
const debugDetails = document.querySelector('#debugDetails');
const debugImage = document.querySelector('#debugImage');

let currentSolution = null;
let previewUrl = null;

function hasInput() {
  return Boolean(screenshotInput.files?.[0] || matrixInput.value.trim());
}

function refreshSolveState() {
  solveButton.disabled = !hasInput();
}

function setBusy(busy) {
  solveButton.disabled = busy || !hasInput();
  solveButtonText.textContent = busy ? 'Solving…' : 'Solve level';
  spinner.classList.toggle('hidden', !busy);
}

function showStatus(kind, title, text) {
  statusCard.classList.remove('hidden', 'error');
  if (kind === 'error') statusCard.classList.add('error');
  statusIcon.textContent = kind === 'error' ? '!' : '✓';
  statusTitle.textContent = title;
  statusText.textContent = text;
}

function resetResult() {
  resultCard.classList.add('hidden');
  currentSolution = null;
}

screenshotInput.addEventListener('change', () => {
  const file = screenshotInput.files?.[0];
  resetResult();
  statusCard.classList.add('hidden');
  if (!file) {
    previewWrap.classList.add('hidden');
    refreshSolveState();
    return;
  }
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(file);
  previewImage.src = previewUrl;
  previewWrap.classList.remove('hidden');
  document.querySelector('.pick').classList.add('hidden');
  refreshSolveState();
});

changeButton.addEventListener('click', () => screenshotInput.click());
matrixInput.addEventListener('input', refreshSolveState);

function metric(label, value) {
  if (value === null || value === undefined || value === '') return '';
  return `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`;
}

async function dataUrlToFile(dataUrl, filename) {
  const response = await fetch(dataUrl);
  const blob = await response.blob();
  return new File([blob], filename, { type: blob.type || 'image/png' });
}

solveButton.addEventListener('click', async () => {
  if (!hasInput()) return;
  setBusy(true);
  resetResult();
  showStatus('ok', 'Working on it', 'Detecting the board and searching for a validated one-line route…');

  const form = new FormData();
  const file = screenshotInput.files?.[0];
  if (file) form.append('screenshot', file, file.name);
  if (matrixInput.value.trim()) form.append('matrix', matrixInput.value.trim());
  if (startInput.value.trim()) form.append('start', startInput.value.trim());

  try {
    const response = await fetch('/api/solve', { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      const message = data.error || `Solver request failed (${response.status}).`;
      showStatus('error', 'Couldn’t solve this one', message);
      if (data.debug_grid) {
        debugImage.src = data.debug_grid;
        debugDetails.classList.remove('hidden');
        resultCard.classList.remove('hidden');
        resultTitle.textContent = 'Detection debug';
        solutionImage.classList.add('hidden');
        document.querySelector('.actions').classList.add('hidden');
        document.querySelector('.metrics').classList.add('hidden');
        document.querySelector('.route-details').classList.add('hidden');
        confidencePill.textContent = 'Needs review';
      }
      return;
    }

    currentSolution = data.solution;
    solutionImage.src = data.solution;
    solutionImage.classList.remove('hidden');
    document.querySelector('.actions').classList.remove('hidden');
    document.querySelector('.metrics').classList.remove('hidden');
    document.querySelector('.route-details').classList.remove('hidden');
    resultTitle.textContent = `${data.tile_count} tiles solved`;
    confidencePill.textContent = data.confidence == null
      ? 'Validated'
      : `${(data.confidence * 100).toFixed(1)}% detection`;

    const shape = Array.isArray(data.grid_shape) ? `${data.grid_shape[0]}×${data.grid_shape[1]}` : null;
    const start = Array.isArray(data.start) ? `(${data.start.join(',')})` : null;
    const end = Array.isArray(data.end) ? `(${data.end.join(',')})` : null;
    const solveMs = typeof data.solve_seconds === 'number'
      ? (data.solve_seconds < 1 ? `${Math.round(data.solve_seconds * 1000)} ms` : `${data.solve_seconds.toFixed(2)} s`)
      : null;

    metrics.innerHTML = [
      metric('Grid', shape),
      metric('Start → End', start && end ? `${start} → ${end}` : null),
      metric('Search', solveMs),
      metric('Validated', data.validated ? 'Yes' : 'No'),
    ].join('');

    routeText.textContent = (data.directions || []).join(' ');
    asciiRoute.textContent = (data.directions_ascii || []).join('');
    if (data.debug_grid) {
      debugImage.src = data.debug_grid;
      debugDetails.classList.remove('hidden');
    } else {
      debugDetails.classList.add('hidden');
    }

    resultCard.classList.remove('hidden');
    const warnings = Array.isArray(data.warnings) && data.warnings.length ? ` ${data.warnings.join(' ')}` : '';
    showStatus('ok', 'Solved and validated', `The path visits every detected tile exactly once.${warnings}`);
    resultCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    showStatus('error', 'Connection problem', 'The website could not reach the solver. Try again in a moment.');
  } finally {
    setBusy(false);
  }
});

shareButton.addEventListener('click', async () => {
  if (!currentSolution) return;
  try {
    const file = await dataUrlToFile(currentSolution, 'one-line-solution.png');
    if (navigator.canShare?.({ files: [file] }) && navigator.share) {
      await navigator.share({ files: [file], title: 'One Line solution' });
    } else {
      openSolution();
    }
  } catch (error) {
    if (error?.name !== 'AbortError') openSolution();
  }
});

function openSolution() {
  if (!currentSolution) return;
  const win = window.open();
  if (win) {
    win.document.write(`<title>One Line solution</title><style>html,body{margin:0;background:#111}img{display:block;max-width:100%;margin:auto}</style><img src="${currentSolution}" alt="One Line solution">`);
    win.document.close();
  } else {
    window.location.href = currentSolution;
  }
}
openButton.addEventListener('click', openSolution);

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/static/sw.js').catch(() => {}));
}
