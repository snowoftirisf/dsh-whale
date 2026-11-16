import test from 'node:test';
import assert from 'node:assert/strict';
import {selectedSpeech,speechCapabilities} from './voice-backend.mjs';

const legacy={available:()=>true},emotional={available:()=>true};
const context={legacy,emotional,emotionalReady:true,nativeVoice:true};

test('emotional voice requires explicit backend, approved reference, ready runtime and native transport',()=>{
  const settings={backend:'indextts2.5',approved:true};
  assert.equal(selectedSpeech(settings,legacy,emotional),emotional);
  assert.deepEqual(speechCapabilities(settings,context),{voice:true,emotion:true});
  for(const change of [{emotionalReady:false},{nativeVoice:false},{emotional:{available:()=>false}},{emotional:null}]){
    assert.deepEqual(speechCapabilities(settings,{...context,...change}),{voice:false,emotion:false});
  }
  assert.deepEqual(speechCapabilities({...settings,approved:false},context),{voice:false,emotion:false});
  assert.deepEqual(speechCapabilities({...settings,emotionDirectionsEnabled:false},context),{voice:true,emotion:false});
});

test('original Qwen selection survives but unknown backends never silently fall back',()=>{
  assert.equal(selectedSpeech({},legacy,emotional),legacy);
  assert.deepEqual(speechCapabilities({approved:true},context),{voice:true,emotion:false});
  assert.deepEqual(speechCapabilities({backend:'not-installed',approved:true},context),{voice:false,emotion:false});
  assert.equal(selectedSpeech({backend:'indextts2.5'},legacy,null),null);
});
