(() => {
  const tabs = [...document.querySelectorAll('.mode-tab')];
  const panels = [...document.querySelectorAll('.tab-panel')];
  const widthRange = document.querySelector('#widthRange');
  const heightRange = document.querySelector('#heightRange');
  const widthValue = document.querySelector('#widthValue');
  const heightValue = document.querySelector('#heightValue');
  const dimensionSummary = document.querySelector('#dimensionSummary');
  const difficultyChoices = document.querySelector('#difficultyChoices');
  const shapeChoices = document.querySelector('#shapeChoices');
  const seedInput = document.querySelector('#seedInput');
  const randomSeedButton = document.querySelector('#randomSeedButton');
  const outputInputs = [
    document.querySelector('#visualOutput'),
    document.querySelector('#pngOutput'),
    document.querySelector('#matrixOutput'),
  ];
  const outputError = document.querySelector('#outputError');
  const generateButton = document.querySelector('#generateButton');
  const generateButtonText = document.querySelector('#generateButtonText');
  const generateSpinner = document.querySelector('#generateSpinner');
  const progressCard = document.querySelector('#generationProgress');
  const progressEyebrow = document.querySelector('#progressEyebrow');
  const progressTitle = document.querySelector('#progressTitle');
  const progressPercent = document.querySelector('#progressPercent');
  const progressTrack = document.querySelector('#progressTrack');
  const progressBar = document.querySelector('#progressBar');
  const progressMessage = document.querySelector('#progressMessage');
  const progressAttempt = document.querySelector('#progressAttempt');
  const progressBest = document.querySelector('#progressBest');
  const progressTarget = document.querySelector('#progressTarget');
  const progressElapsed = document.querySelector('#progressElapsed');
  const cancelButton = document.querySelector('#cancelGenerationButton');
  const errorCard = document.querySelector('#generatorError');
  const errorTitle = document.querySelector('#generatorErrorTitle');
  const errorText = document.querySelector('#generatorErrorText');
  const retryButton = document.querySelector('#retryGenerationButton');
  const resultCard = document.querySelector('#generatorResult');
  const generatedTier = document.querySelector('#generatedTier');
  const generatedMetrics = document.querySelector('#generatedMetrics');
  const generatedSeed = document.querySelector('#generatedSeed');
  const copySeedButton = document.querySelector('#copySeedButton');
  const visualGridBlock = document.querySelector('#visualGridBlock');
  const visualGrid = document.querySelector('#visualGrid');
  const pngBlock = document.querySelector('#pngBlock');
  const generatedPng = document.querySelector('#generatedPng');
  const downloadPng = document.querySelector('#downloadPng');
  const openPng = document.querySelector('#openPng');
  const matrixBlock = document.querySelector('#matrixBlock');
  const generatedMatrix = document.querySelector('#generatedMatrix');
  const matrixStart = document.querySelector('#matrixStart');
  const copyMatrixButton = document.querySelector('#copyMatrixButton');
  const revealButton = document.querySelector('#revealSolutionButton');
  const revealedSolution = document.querySelector('#revealedSolution');
  const solutionGrid = document.querySelector('#solutionGrid');
  const generatedArrows = document.querySelector('#generatedArrows');
  const generatedCoordinates = document.querySelector('#generatedCoordinates');
  const downloadSolvedPng = document.querySelector('#downloadSolvedPng');
  const difficultyWhy = document.querySelector('#difficultyWhy');
  const generatedDetails = document.querySelector('#generatedDetails');
  const downloadJson = document.querySelector('#downloadJson');

  let activeJobId = null;
  let pollTimer = null;
  let activeResult = null;

  function selectPanel(panelId, updateHash = true) {
    tabs.forEach(tab => {
      const active = tab.dataset.panel === panelId;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
    });
    panels.forEach(panel => panel.classList.toggle('hidden', panel.id !== panelId));
    if (updateHash) history.replaceState(null, '', panelId === 'generatorPanel' ? '#generator' : '#solver');
  }

  tabs.forEach(tab => tab.addEventListener('click', () => selectPanel(tab.dataset.panel)));
  selectPanel(location.hash === '#generator' ? 'generatorPanel' : 'solverPanel', false);

  function updateDimensions() {
    widthValue.textContent = widthRange.value;
    heightValue.textContent = heightRange.value;
    dimensionSummary.textContent = `${widthRange.value} × ${heightRange.value}`;
  }

  [widthRange, heightRange].forEach(input => input.addEventListener('input', updateDimensions));
  document.querySelectorAll('.dimension-control .stepper').forEach(button => {
    button.addEventListener('click', () => {
      const group = button.closest('.dimension-control');
      const input = group.querySelector('input[type="range"]');
      const next = Number(input.value) + Number(button.dataset.step);
      input.value = String(Math.max(Number(input.min), Math.min(Number(input.max), next)));
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  });
  updateDimensions();

  function setupChoiceGroup(group) {
    group.querySelectorAll('button').forEach(button => {
      button.addEventListener('click', () => {
        group.querySelectorAll('button').forEach(item => {
          const selected = item === button;
          item.classList.toggle('selected', selected);
          item.setAttribute('aria-pressed', String(selected));
        });
      });
    });
  }
  setupChoiceGroup(difficultyChoices);
  setupChoiceGroup(shapeChoices);

  function randomSeed() {
    const values = new Uint32Array(2);
    crypto.getRandomValues(values);
    seedInput.value = `${values[0]}${String(values[1]).padStart(10, '0')}`.slice(0, 18);
  }
  randomSeedButton.addEventListener('click', randomSeed);

  outputInputs.forEach(input => input.addEventListener('change', () => {
    if (!outputInputs.some(item => item.checked)) {
      input.checked = true;
      outputError.classList.remove('hidden');
      window.setTimeout(() => outputError.classList.add('hidden'), 2400);
    } else {
      outputError.classList.add('hidden');
    }
  }));

  function selectedValue(group) {
    return group.querySelector('.selected')?.dataset.value;
  }

  function requestBody() {
    return {
      width: Number(widthRange.value),
      height: Number(heightRange.value),
      difficulty: selectedValue(difficultyChoices),
      shape_mode: selectedValue(shapeChoices),
      seed: seedInput.value.trim() || null,
      outputs: {
        visual_grid: outputInputs[0].checked,
        polished_png: outputInputs[1].checked,
        binary_matrix: outputInputs[2].checked,
      },
    };
  }

  function setGenerating(active) {
    generateButton.disabled = active;
    generateButtonText.textContent = active ? 'Generating…' : 'Generate level';
    generateSpinner.classList.toggle('hidden', !active);
    cancelButton.disabled = !active;
  }

  function titleCase(value) {
    return value ? value[0].toUpperCase() + value.slice(1) : '';
  }

  function phaseLabel(phase) {
    const labels = {
      queued: 'Queued',
      initialize: 'Initializing',
      constructing_topology: 'Constructing topology',
      checking_solution: 'Checking solvability',
      checking_uniqueness: 'Proving uniqueness',
      scoring_human_difficulty: 'Scoring human difficulty',
      validating_outputs: 'Validating outputs',
      complete: 'Complete',
      cancelled: 'Cancelled',
      budget_exhausted: 'Generation budget reached',
    };
    return labels[phase] || String(phase || 'Working').replaceAll('_', ' ');
  }

  function showProgress(data, fallbackSettings = null) {
    progressCard.classList.remove('hidden');
    errorCard.classList.add('hidden');
    resultCard.classList.add('hidden');
    const body = fallbackSettings || requestBody();
    const percent = Math.max(0, Math.min(100, Number(data.percent || 0)));
    progressEyebrow.textContent = `Generating ${titleCase(body.difficulty)} ${body.width}×${body.height}…`;
    progressTitle.textContent = phaseLabel(data.phase);
    progressPercent.textContent = `${percent}%`;
    progressTrack.setAttribute('aria-valuenow', String(percent));
    progressBar.style.width = `${percent}%`;
    progressMessage.textContent = data.message || 'Working on a validated puzzle…';
    progressAttempt.textContent = data.attempt || 0;
    progressBest.textContent = data.best_score == null ? '—' : `${data.best_score} / 600`;
    progressTarget.textContent = Array.isArray(data.target_range) ? data.target_range.join('–') : '—';
    progressElapsed.textContent = `${Number(data.elapsed_seconds || 0).toFixed(1)} s`;
  }

  function showGeneratorError(title, message) {
    errorTitle.textContent = title;
    errorText.textContent = message;
    errorCard.classList.remove('hidden');
    progressCard.classList.add('hidden');
    resultCard.classList.add('hidden');
    setGenerating(false);
  }

  function addMetric(container, label, value) {
    if (value === null || value === undefined) return;
    const item = document.createElement('div');
    item.className = 'metric';
    const caption = document.createElement('span');
    caption.textContent = label;
    const strong = document.createElement('strong');
    strong.textContent = String(value);
    item.append(caption, strong);
    container.append(item);
  }

  function renderResult(status) {
    activeResult = status.result;
    const result = status.result;
    progressCard.classList.add('hidden');
    errorCard.classList.add('hidden');
    resultCard.classList.remove('hidden');
    setGenerating(false);

    generatedTier.textContent = `${titleCase(result.difficulty.tier)} — ${result.difficulty.score} / 600`;
    generatedMetrics.replaceChildren();
    addMetric(generatedMetrics, 'Dimensions', `${result.width} × ${result.height}`);
    addMetric(generatedMetrics, 'Tiles', result.tile_count);
    addMetric(generatedMetrics, 'Start', `(${result.start.join(', ')})`);
    addMetric(generatedMetrics, 'Shape', titleCase(result.shape_mode));
    generatedSeed.textContent = result.seed;

    if (result.visual_grid_svg) {
      visualGrid.innerHTML = result.visual_grid_svg;
      visualGridBlock.classList.remove('hidden');
    } else {
      visualGridBlock.classList.add('hidden');
      visualGrid.replaceChildren();
    }

    const pngUrl = result.downloads?.unsolved_png;
    if (pngUrl) {
      generatedPng.src = pngUrl;
      downloadPng.href = pngUrl;
      openPng.href = pngUrl;
      pngBlock.classList.remove('hidden');
    } else {
      pngBlock.classList.add('hidden');
      generatedPng.removeAttribute('src');
    }

    if (result.matrix_text) {
      generatedMatrix.textContent = result.matrix_text;
      matrixStart.textContent = `(${result.start.join(', ')})`;
      matrixBlock.classList.remove('hidden');
    } else {
      matrixBlock.classList.add('hidden');
      generatedMatrix.textContent = '';
    }

    revealedSolution.classList.add('hidden');
    revealButton.classList.remove('hidden');
    revealButton.disabled = false;
    revealButton.textContent = 'Reveal solution';
    solutionGrid.replaceChildren();

    difficultyWhy.replaceChildren();
    const heading = document.createElement('h3');
    heading.textContent = 'Why this score';
    const list = document.createElement('ul');
    (result.difficulty.explanation || []).forEach(reason => {
      const item = document.createElement('li');
      item.textContent = reason;
      list.append(item);
    });
    difficultyWhy.append(heading, list);

    generatedDetails.replaceChildren();
    addMetric(generatedDetails, 'Forced moves', `${Math.round(result.difficulty.forced_move_ratio * 100)}%`);
    addMetric(generatedDetails, 'Branch points', result.difficulty.branch_points);
    addMetric(generatedDetails, 'Longest trap', `${result.difficulty.maximum_wrong_branch_survival} moves`);
    addMetric(generatedDetails, 'Turns', result.details.turn_count);
    addMetric(generatedDetails, 'Attempts', result.details.generation_attempts);
    addMetric(generatedDetails, 'Uniqueness nodes', result.details.uniqueness_nodes_explored);
    addMetric(generatedDetails, 'Generation', `${Number(result.details.generation_time).toFixed(2)} s`);
    addMetric(generatedDetails, 'Validated', result.validated ? 'Yes' : 'No');
    downloadJson.href = result.downloads.json;

    resultCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function pollJob(jobId, settings = null) {
    window.clearTimeout(pollTimer);
    try {
      const response = await fetch(`/api/jobs/${jobId}`, { cache: 'no-store' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || 'Generation status unavailable.');
      if (data.state === 'complete') {
        renderResult(data);
        return;
      }
      if (data.state === 'failed') {
        showGeneratorError('Couldn’t meet those constraints', data.error || data.message);
        return;
      }
      if (data.state === 'cancelled') {
        showGeneratorError('Generation cancelled', 'No puzzle was returned and the worker stopped.');
        return;
      }
      showProgress(data, settings);
      setGenerating(true);
      pollTimer = window.setTimeout(() => pollJob(jobId, settings), 350);
    } catch (error) {
      showGeneratorError('Connection problem', error.message || 'Could not read generation progress.');
    }
  }

  async function startGeneration() {
    const body = requestBody();
    if (body.seed && !/^[A-Za-z0-9_-]{1,64}$/.test(body.seed)) {
      showGeneratorError('Seed needs a small edit', 'Use only letters, numbers, underscores, or hyphens.');
      return;
    }
    setGenerating(true);
    resultCard.classList.add('hidden');
    errorCard.classList.add('hidden');
    showProgress({ percent: 0, phase: 'queued', message: 'Submitting one generation job…', target_range: [] }, body);
    try {
      const response = await fetch('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || `Generation request failed (${response.status}).`);
      activeJobId = data.job_id;
      seedInput.value = data.seed;
      localStorage.setItem('oneLineGeneratorJobId', activeJobId);
      await pollJob(activeJobId, { ...body, seed: data.seed });
    } catch (error) {
      showGeneratorError('Couldn’t start generation', error.message || 'Try again in a moment.');
    }
  }

  generateButton.addEventListener('click', startGeneration);
  retryButton.addEventListener('click', startGeneration);

  cancelButton.addEventListener('click', async () => {
    if (!activeJobId) return;
    cancelButton.disabled = true;
    cancelButton.textContent = 'Cancelling…';
    try {
      await fetch(`/api/jobs/${activeJobId}/cancel`, { method: 'POST' });
      await pollJob(activeJobId);
    } finally {
      cancelButton.textContent = 'Cancel generation';
    }
  });

  async function copyText(text, button, original) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (error) {
      const area = document.createElement('textarea');
      area.value = text;
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.append(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    button.textContent = 'Copied ✓';
    window.setTimeout(() => { button.textContent = original; }, 1400);
  }

  copySeedButton.addEventListener('click', () => copyText(generatedSeed.textContent, copySeedButton, 'Copy'));
  copyMatrixButton.addEventListener('click', () => copyText(generatedMatrix.textContent, copyMatrixButton, 'Copy'));

  revealButton.addEventListener('click', async () => {
    if (!activeJobId) return;
    revealButton.disabled = true;
    revealButton.textContent = 'Loading solution…';
    try {
      const response = await fetch(`/api/jobs/${activeJobId}/solution`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || 'Solution unavailable.');
      solutionGrid.innerHTML = data.solution_svg;
      generatedArrows.textContent = (data.route.arrows || []).join(' ');
      generatedCoordinates.textContent = JSON.stringify(data.route.coordinates, null, 2);
      if (data.solved_png) {
        downloadSolvedPng.href = data.solved_png;
        downloadSolvedPng.classList.remove('hidden');
      } else {
        downloadSolvedPng.classList.add('hidden');
      }
      revealedSolution.classList.remove('hidden');
      revealButton.classList.add('hidden');
    } catch (error) {
      revealButton.disabled = false;
      revealButton.textContent = 'Reveal solution';
      showGeneratorError('Couldn’t reveal the solution', error.message);
    }
  });

  const savedJobId = localStorage.getItem('oneLineGeneratorJobId');
  if (savedJobId) {
    activeJobId = savedJobId;
    pollJob(savedJobId).catch(() => {});
  }
})();
