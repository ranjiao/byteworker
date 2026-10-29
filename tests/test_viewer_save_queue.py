import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'node required')
class ViewerSaveQueueTests(unittest.TestCase):
    def test_revision_queue_navigation_and_failure(self):
        page = (ROOT / 'viewer/index.html').read_text()
        source = page[page.index('async function closeSettings()'):page.index('function scheduleValue')]
        script = '''
const assert = require('assert');
const API_TOKEN = 'secret';
const state = {settingsOpen:true, settingsTab:'global', appSettings:{}, appSettingsRevision:0};
let checked = false, renders = 0, requests = [];
let discard = false;
const confirmations = [];
function confirm(message){ confirmations.push(message); return discard; }
const D = {settingsPortal:{innerHTML:'open'}};
const document = {getElementById: id => ({checked, value:'UTC'})};
function renderSettings(){ renders++; }
function updateSettingsStatus(){}
function fetch(url, options) {
  assert.equal(options.headers['X-Byteworker-Token'], 'secret');
  return new Promise(resolve => requests.push({body:JSON.parse(options.body), resolve}));
}
function succeed(request){ request.resolve({ok:true,json:async()=>({persisted:request.body})}); }
''' + source + '''
(async()=>{
  scheduleSystemSettingsSave();
  const first = saveSystemSettings();
  checked = true;
  scheduleSystemSettingsSave();
  assert.equal(requests.length,1);
  succeed(requests[0]);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,2);
  assert.equal(state.appSettingsDirty,true);
  assert.equal(requests[1].body.viewer.access_token_required,true);
  succeed(requests[1]); await first;
  assert.equal(state.appSettingsDirty,false);
  checked=false; scheduleSystemSettingsSave();
  const closing=closeSettings();
  assert.equal(state.settingsOpen,true);
  assert.equal(confirmations.length,0);
  requests[2].resolve({ok:false,json:async()=>({error:{message:'disk full'}})});
  await closing;
  assert.equal(state.settingsOpen,true);
  assert.equal(state.appSettingsDirty,true);
  assert.equal(state.appSettingsError,'disk full');
  assert.equal(confirmations.length,1);
  assert.ok(confirmations[0].includes('disk full'));
  assert.notEqual(state.appSettingsPending,null);
  const switching=setSettingsTab('display');
  succeed(requests[3]); await switching;
  assert.equal(state.settingsTab,'display');
  assert.equal(state.appSettingsDirty,false);
  assert.equal(confirmations.length,1); // Successful flush needs no confirmation.
  state.settingsTab='global';
  scheduleSystemSettingsSave();
  const deniedSwitch=setSettingsTab('display');
  requests[4].resolve({ok:false,json:async()=>({error:{message:'invalid timezone'}})});
  await deniedSwitch;
  assert.equal(state.settingsTab,'global');
  assert.equal(state.appSettingsDirty,true);
  assert.equal(state.appSettingsError,'invalid timezone');
  assert.ok(confirmations[1].includes('invalid timezone'));
  discard=true;
  const discardedSwitch=setSettingsTab('display');
  requests[5].resolve({ok:false,json:async()=>({error:{message:'invalid timezone'}})});
  await discardedSwitch;
  assert.equal(state.settingsTab,'display');
  assert.equal(state.appSettingsDirty,false);
  assert.equal(state.appSettingsPending,null);
  assert.equal(state.appSettingsSaveTimer,null);
  assert.equal(state.appSettingsError,'');
  state.settingsTab='global';
  scheduleSystemSettingsSave();
  const discardedClose=closeSettings();
  requests[6].resolve({ok:false,json:async()=>({error:{message:'server down'}})});
  await discardedClose;
  assert.equal(state.settingsOpen,false);
  assert.equal(state.appSettingsDirty,false);
  assert.equal(state.appSettingsPending,null);
  assert.equal(state.appSettingsSaveTimer,null);
  assert.equal(state.appSettingsError,'');
  assert.equal(confirmations.length,4);
  assert.ok(confirmations[3].includes('server down'));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
