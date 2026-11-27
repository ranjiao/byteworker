// The settings dialog delegates reads, field changes and navigation to this controller.
function createSettingsController({state, D, apiToken, renderSettings, updateSettingsStatus}) {
  async function closeSettings() {
    if (!(await flushSettings(true))) return;
    state.settingsOpen = false;
    D.settingsPortal.innerHTML = '';
    D.app.inert = false;
    D.palettePortal.inert = false;
    D.btnSettings.focus();
  }
  async function settingsApi(method, body) {
    const options = {
      method,
      headers: {
        'Accept': 'application/json',
      },
      cache: 'no-store',
    };
    if (apiToken) options.headers['X-Byteworker-Token'] = apiToken;
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch('/api/settings', options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.error) {
      const err = payload.error || {};
      throw new Error((err.message || err.code || '设置接口请求失败。') + (err.trace_id ? ` 诊断编号 ${err.trace_id}` : ''));
    }
    return payload;
  }
  async function loadSystemSettings() {
    if (!state.settingsOpen || state.settingsTab === 'display') return;
    if (state.appSettingsDirty && !(await flushSettings())) return;
    state.appSettingsLoading = true;
    state.appSettingsError = '';
    state.appSettingsSavedTab = '';
    renderSettings(true);
    try {
      state.appSettings = await settingsApi('GET');
      state.appSettingsDirty = false;
    } catch (e) {
      state.appSettingsError = e.message || String(e);
    } finally {
      state.appSettingsLoading = false;
      renderSettings(true);
    }
  }
  function clearSettingsSaveTimer() {
    if (state.appSettingsSaveTimer) {
      clearTimeout(state.appSettingsSaveTimer);
      state.appSettingsSaveTimer = null;
    }
  }
  function scheduleSystemSettingsSave() {
    if (!state.settingsOpen || state.settingsTab === 'display') return;
    state.appSettingsDirty = true;
    state.appSettingsRevision++;
    state.appSettingsPending = { values: collectSettingsValues(), revision: state.appSettingsRevision };
    state.appSettingsError = '';
    clearSettingsSaveTimer();
    state.appSettingsSaveTimer = setTimeout(() => {
      state.appSettingsSaveTimer = null;
      saveSystemSettings();
    }, 250);
    updateSettingsStatus();
  }
  async function setSettingsTab(tab) {
    if (!(await flushSettings(true))) return;
    state.settingsTab = tab;
    renderSettings();
    if (tab !== 'display' && !state.appSettings && !state.appSettingsLoading) {
      loadSystemSettings();
    }
  }
  function inputValue(id) {
    return document.getElementById(id)?.value ?? '';
  }
  function inputChecked(id) {
    return !!document.getElementById(id)?.checked;
  }
  function numberValue(id, fallback) {
    const parsed = Number(inputValue(id));
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  function collectSettingsValues() {
    let patch;
    if (state.settingsTab === 'global') {
      patch = {
        viewer: {
          access_token_required: inputChecked('settings-viewer-token-required'),
        },
        dreaming: {
          timezone: inputValue('settings-dreaming-timezone'),
        },
      };
    } else if (state.settingsTab === 'digest') {
      patch = {
        sources: Array.from(D.settingsPortal.querySelectorAll('[data-source-uid]')).map(row => {
          const enabled = row.querySelector('[data-source-enabled]')?.checked || false;
          const cadence = row.querySelector('[data-source-cadence]')?.value || '';
          return {
            source_uid: row.dataset.sourceUid,
            routine: {
              enabled,
              cadence: enabled ? (cadence || 'daily') : '',
            },
          };
        }),
      };
    } else if (state.settingsTab === 'dreaming') {
      const processKind = inputValue('settings-process-kind') || 'interval';
      const processSchedule = { kind: processKind };
      if (processKind === 'interval') {
        processSchedule.minutes = numberValue('settings-process-minutes', 120);
      } else if (processKind === 'daily_time') {
        processSchedule.time = inputValue('settings-process-time-daily') || '22:00';
      } else {
        processSchedule.days = numberValue('settings-process-days', 2);
        processSchedule.time = inputValue('settings-process-time-days') || '22:00';
      }
      patch = {
        dreaming: {
          jobs: {
            process: {
              enabled: inputChecked('settings-process-enabled'),
              schedule: processSchedule,
            },
            morning: {
              enabled: inputChecked('settings-morning-enabled'),
              schedule: { time: inputValue('settings-morning-time') || '08:30' },
            },
            maintenance: {
              enabled: inputChecked('settings-maintenance-enabled'),
              schedule: { time: inputValue('settings-maintenance-time') || '03:30' },
            },
            recovery: {
              enabled: inputChecked('settings-recovery-enabled'),
              schedule: { minutes: numberValue('settings-recovery-minutes', 240) },
            },
          },
          im: {
            mode: inputValue('settings-im-mode') || 'off',
            persist_finding: inputChecked('settings-im-persist'),
            acknowledge_all_visible: inputChecked('settings-im-all-visible-ack'),
          },
          logging: {
            retention_days: numberValue('settings-log-retention', 30),
          },
          delivery: {
            lark_summary_enabled: inputChecked('settings-lark-enabled'),
            lark_recipient: inputValue('settings-lark-recipient'),
          },
          harness_preferences: {
            wake_interval_minutes: numberValue('settings-harness-wake-minutes', 120),
            model: inputValue('settings-harness-model'),
          },
        },
      };
    } else {
      return;
    }
    return patch;
  }
  function changedSettingsFields(before, after) {
    const changes = {};
    for (const [key, value] of Object.entries(after)) {
      const previous = before[key];
      // The settings writers take a complete schedule or source routine.
      if (key === 'sources') {
        const routines = new Map((previous || []).map(row => [row.source_uid, row.routine]));
        const rows = value.filter(row =>
          JSON.stringify(row.routine) !== JSON.stringify(routines.get(row.source_uid)));
        if (rows.length) changes[key] = rows;
      } else if (key === 'schedule') {
        if (JSON.stringify(value) !== JSON.stringify(previous)) changes[key] = value;
      } else if (value && typeof value === 'object') {
        const fields = changedSettingsFields(previous || {}, value);
        if (Object.keys(fields).length) changes[key] = fields;
      } else if (key !== 'acknowledge_all_visible' && value !== previous) {
        changes[key] = value;
      }
    }
    return changes;
  }
  function collectSettingsPatch(values) {
    const patch = changedSettingsFields(state.appSettingsBaseline, values);
    if (patch.dreaming?.im && values.dreaming.im.mode === 'all_visible' &&
        values.dreaming.im.acknowledge_all_visible) {
      patch.dreaming.im.acknowledge_all_visible = true;
    }
    if (patch.dreaming && state.settingsTab === 'dreaming' && !state.appSettings?.dreaming?.timezone) {
      patch.dreaming.timezone = settingsTimezone();
    }
    return patch;
  }
  async function flushSettings(confirmDiscard = false) {
    clearSettingsSaveTimer();
    await saveSystemSettings();
    if (!state.appSettingsDirty) return true;
    if (!confirmDiscard || !confirm(`保存失败：${state.appSettingsError || '未知错误'}\n是否放弃尚未保存的修改并继续？`)) return false;
    clearSettingsSaveTimer();
    state.appSettingsPending = null;
    state.appSettingsDirty = false;
    state.appSettingsError = '';
    return true;
  }
  function saveSystemSettings() {
    if (state.appSettingsSavePromise) return state.appSettingsSavePromise;
    if (!state.appSettingsPending) return Promise.resolve();
    state.appSettingsSavePromise = (async () => {
      state.appSettingsSaving = true;
      let saved = false;
      try {
        while (state.appSettingsPending) {
          const draft = state.appSettingsPending;
          state.appSettingsError = '';
          updateSettingsStatus();
          const patch = collectSettingsPatch(draft.values);
          if (Object.keys(patch).length) {
            state.appSettings = await settingsApi('PATCH', patch);
            saved = true;
          }
          state.appSettingsBaseline = draft.values;
          if (draft.revision === state.appSettingsRevision) {
            state.appSettingsPending = null;
            state.appSettingsDirty = false;
            state.appSettingsSavedTab = state.settingsTab;
          }
        }
      } catch (e) {
        state.appSettingsError = e.message || String(e);
      } finally {
        state.appSettingsSaving = false;
        if (!state.appSettingsDirty && saved) renderSettings(true);
        else updateSettingsStatus();
      }
    })().finally(() => {
      state.appSettingsSavePromise = null;
    });
    return state.appSettingsSavePromise;
  }
  function settingsTimezone() {
    const configured = state.appSettings?.dreaming?.timezone;
    if (configured) return configured;
    const hint = state.appSettings?.kb?.context?.timezone_hint;
    if (hint) {
      try {
        return new Intl.DateTimeFormat('zh-CN', { timeZone: hint }).resolvedOptions().timeZone;
      } catch (e) { /* Use the default for an invalid context hint. */ }
    }
    return 'Asia/Shanghai';
  }

  return {
    closeSettings, loadSystemSettings, setSettingsTab, scheduleSystemSettingsSave,
    collectSettingsValues, settingsTimezone, flushSettings,
  };
}
