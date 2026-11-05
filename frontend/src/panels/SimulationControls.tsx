import React from 'react';
import { Play, Pause, RotateCcw, StepForward, FastForward, Sliders, AlertCircle } from 'lucide-react';
import { ScenarioDefinition } from '../types/api';

interface SimulationControlsProps {
  isPlaying: boolean;
  onPlay: () => void;
  onPause: () => void;
  onStep: () => void;
  onReset: () => void;
  speed: number;
  onSpeedChange: (speed: number) => void;
  scenarios: ScenarioDefinition[];
  currentScenarioId: string;
  onSelectScenario: (scenarioId: string) => void;
  onTriggerRouteSwitch: () => void;
  isRouteSwitchActive: boolean;
}

export const SimulationControls: React.FC<SimulationControlsProps> = ({
  isPlaying,
  onPlay,
  onPause,
  onStep,
  onReset,
  speed,
  onSpeedChange,
  scenarios,
  currentScenarioId,
  onSelectScenario,
  onTriggerRouteSwitch,
  isRouteSwitchActive,
}) => {
  const speeds = [0.5, 1, 2, 5, 10];

  return (
    <div className="w-full bg-[#0b0f17] border border-slate-800 rounded-lg p-3 flex flex-wrap items-center justify-between gap-3 shadow-lg select-none font-mono text-xs">
      {/* Playback Controls */}
      <div className="flex items-center gap-2">
        {isPlaying ? (
          <button
            onClick={onPause}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 rounded font-bold transition-all shadow-sm"
          >
            <Pause className="w-3.5 h-3.5 fill-current" />
            <span>PAUSE</span>
          </button>
        ) : (
          <button
            onClick={onPlay}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 border border-cyan-500/40 rounded font-bold transition-all shadow-sm"
          >
            <Play className="w-3.5 h-3.5 fill-current" />
            <span>PLAY</span>
          </button>
        )}

        <button
          onClick={onStep}
          disabled={isPlaying}
          className="flex items-center gap-1 px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded font-medium transition-all disabled:opacity-40"
          title="Advance 1 simulation tick"
        >
          <StepForward className="w-3.5 h-3.5" />
          <span>STEP</span>
        </button>

        <button
          onClick={onReset}
          className="flex items-center gap-1 px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded font-medium transition-all"
          title="Reset simulation to initial state"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          <span>RESET</span>
        </button>
      </div>

      {/* Speed Selector */}
      <div className="flex items-center gap-1 bg-slate-900 border border-slate-800 rounded p-1">
        <span className="text-[10px] uppercase text-slate-500 px-1.5">Speed:</span>
        {speeds.map((s) => (
          <button
            key={s}
            onClick={() => onSpeedChange(s)}
            className={`px-2 py-0.5 rounded text-[11px] font-semibold transition-all ${
              speed === s
                ? 'bg-cyan-500/30 text-cyan-300 border border-cyan-500/50'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {s}x
          </button>
        ))}
      </div>

      {/* Scenario Selector */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase text-slate-500">Scenario:</span>
        <select
          value={currentScenarioId}
          onChange={(e) => onSelectScenario(e.target.value)}
          className="bg-slate-900 border border-slate-700 rounded px-2.5 py-1 text-slate-200 text-xs font-mono focus:border-cyan-400 outline-none cursor-pointer"
        >
          {scenarios.map((scen) => (
            <option key={scen.id} value={scen.id} className="bg-slate-900 text-slate-200">
              {scen.name}
            </option>
          ))}
        </select>

        {/* Congestion Route Switch Toggle Button */}
        <button
          onClick={onTriggerRouteSwitch}
          className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-mono font-bold transition-all border shadow-sm ${
            isRouteSwitchActive
              ? 'bg-amber-500/20 text-amber-300 border-amber-500/60'
              : 'bg-slate-800 hover:bg-slate-700 text-slate-300 border-slate-700'
          }`}
          title="Simulate congestion on E007 and view dynamic route switch"
        >
          <AlertCircle className="w-3.5 h-3.5 text-amber-400" />
          <span>{isRouteSwitchActive ? 'REROUTE ACTIVE' : 'TEST REROUTE'}</span>
        </button>
      </div>
    </div>
  );
};
