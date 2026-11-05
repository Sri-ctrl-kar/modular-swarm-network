import React from 'react';
import { Activity, Cpu, Sparkles, BarChart3, Play, Film } from 'lucide-react';

interface TopBarProps {
  scenarioName: string;
  timeMin: number;
  speed: number;
  isPlaying: boolean;
  providerType: 'mock' | 'live';
  onOpenComparison: () => void;
  onStartDemo: () => void;
  isDemoRunning: boolean;
}

export const TopBar: React.FC<TopBarProps> = ({
  scenarioName,
  timeMin,
  speed,
  isPlaying,
  providerType,
  onOpenComparison,
  onStartDemo,
  isDemoRunning,
}) => {
  const hours = Math.floor(timeMin / 60);
  const minutes = Math.floor(timeMin % 60);
  const timeFormatted = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;

  return (
    <header className="w-full bg-[#0a0e16] border-b border-slate-800/90 px-5 py-2.5 flex items-center justify-between shadow-md z-30 select-none">
      {/* Left: Brand & Product Objective */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2.5">
          <div className="w-3 h-3 rounded-full bg-cyan-400 shadow-[0_0_10px_#00f0ff] animate-pulse"></div>
          <div>
            <h1 className="text-sm font-extrabold tracking-wider text-slate-100 uppercase font-mono">
              Modular Swarm Network
            </h1>
            <p className="text-[10px] tracking-widest text-cyan-400/90 uppercase font-mono font-medium -mt-0.5">
              Adaptive Urban Mobility Intelligence
            </p>
          </div>
        </div>

        {/* Live Simulation Indicator */}
        <div className="hidden md:flex items-center gap-2 px-2.5 py-1 rounded bg-slate-900 border border-slate-800 text-[11px] font-mono">
          <span className={`w-2 h-2 rounded-full ${isPlaying ? 'bg-emerald-400 animate-ping' : 'bg-amber-400'}`}></span>
          <span className={isPlaying ? 'text-emerald-300 font-semibold' : 'text-slate-400'}>
            {isPlaying ? 'SIMULATION LIVE' : 'SIMULATION PAUSED'}
          </span>
        </div>

        {/* AI Provider Status Badge (MOCK vs LIVE) */}
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-900 border border-slate-800 text-[11px] font-mono">
          <Cpu className={`w-3.5 h-3.5 ${providerType === 'live' ? 'text-purple-400' : 'text-cyan-400'}`} />
          <span className="text-slate-400">GEMINI:</span>
          {providerType === 'live' ? (
            <span className="text-purple-300 font-bold flex items-center gap-1">
              <Sparkles className="w-3 h-3 text-purple-400" />
              LIVE GEMINI
            </span>
          ) : (
            <span className="text-cyan-300 font-semibold">MOCK GEMINI</span>
          )}
        </div>
      </div>

      {/* Right: Scenario, Clock, Speed, Actions */}
      <div className="flex items-center gap-3">
        {/* Scenario Tag */}
        <div className="hidden lg:flex flex-col text-right">
          <span className="text-[9px] uppercase tracking-wider text-slate-500 font-mono">Scenario</span>
          <span className="text-xs font-mono font-semibold text-slate-200">{scenarioName}</span>
        </div>

        {/* Clock */}
        <div className="flex flex-col text-right px-3 py-0.5 bg-slate-900/90 rounded border border-slate-800">
          <span className="text-[9px] uppercase tracking-wider text-slate-500 font-mono">Sim Time</span>
          <span className="text-sm font-mono font-bold text-cyan-300 tracking-wider">
            {timeFormatted} <span className="text-[10px] text-slate-400 font-normal">({timeMin.toFixed(1)}m)</span>
          </span>
        </div>

        {/* Speed */}
        <div className="hidden sm:flex items-center px-2.5 py-1 rounded bg-slate-900 border border-slate-800 text-xs font-mono text-slate-300">
          <span className="text-slate-500 mr-1 text-[10px] uppercase">Speed</span>
          <span className="font-bold text-cyan-400">{speed}x</span>
        </div>

        {/* Scenario Comparison Button */}
        <button
          onClick={onOpenComparison}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 hover:border-slate-600 text-xs font-mono font-medium transition-all shadow-sm"
          title="Compare Baseline, Swarm, and Adaptive scenarios"
        >
          <BarChart3 className="w-3.5 h-3.5 text-cyan-400" />
          <span>COMPARE</span>
        </button>

        {/* Demo Mode Button */}
        <button
          onClick={onStartDemo}
          disabled={isDemoRunning}
          className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded text-xs font-mono font-bold tracking-wider transition-all shadow-md ${
            isDemoRunning
              ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse'
              : 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/50 hover:bg-cyan-500/30'
          }`}
          title="Run 40-second competition judge showcase"
        >
          <Film className="w-3.5 h-3.5 text-cyan-400" />
          <span>{isDemoRunning ? 'DEMO RUNNING...' : 'RUN DEMO'}</span>
        </button>
      </div>
    </header>
  );
};
