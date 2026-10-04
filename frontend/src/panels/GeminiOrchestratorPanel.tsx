import React, { useState } from 'react';
import {
  Cpu,
  CheckCircle2,
  XCircle,
  Sparkles,
  ArrowDown,
  Info,
  ChevronDown,
  ChevronUp,
  ShieldCheck,
  ShieldAlert,
  Play,
  RotateCw,
} from 'lucide-react';
import { OrchestrationObservationData, CycleAuditRecord } from '../types/api';

interface GeminiOrchestratorPanelProps {
  observation: OrchestrationObservationData | null;
  lastRecord: CycleAuditRecord | null;
  providerType: 'mock' | 'live';
  onRunCycle: () => Promise<void>;
  isAnalyzing: boolean;
}

export const GeminiOrchestratorPanel: React.FC<GeminiOrchestratorPanelProps> = ({
  observation,
  lastRecord,
  providerType,
  onRunCycle,
  isAnalyzing,
}) => {
  const [showWhyReasoning, setShowWhyReasoning] = useState(false);
  const [showChecksList, setShowChecksList] = useState(false);

  // Extract observation highlights
  const topDeficitNode = observation?.demand?.top_deficit_nodes?.[0] || null;
  const headline = observation?.headline || 'No active observation';
  const fingerprint = observation?.fingerprint || '—';

  // Last cycle data
  const proposal = lastRecord?.proposal;
  const isApproved = lastRecord?.verdict === 'APPROVED';
  const isRejected = lastRecord?.verdict === 'REJECTED';

  return (
    <div className="w-full flex flex-col bg-[#0b0f17] border border-slate-800 rounded-lg overflow-hidden shadow-lg">
      {/* Panel Header */}
      <div className="bg-[#0f141f] border-b border-slate-800 px-4 py-2.5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cpu className={`w-4 h-4 ${providerType === 'live' ? 'text-purple-400' : 'text-cyan-400'}`} />
          <span className="text-xs font-mono font-bold tracking-wider text-slate-200 uppercase">
            Gemini Orchestrator
          </span>
          <span
            className={`text-[9px] px-1.5 py-0.5 rounded font-mono font-bold tracking-wider uppercase border ${
              providerType === 'live'
                ? 'bg-purple-900/40 text-purple-300 border-purple-600/50'
                : 'bg-cyan-950/40 text-cyan-300 border-cyan-700/50'
            }`}
          >
            ● {providerType === 'live' ? 'LIVE GEMINI' : 'MOCK GEMINI'}
          </span>
        </div>

        <button
          onClick={onRunCycle}
          disabled={isAnalyzing}
          className="flex items-center gap-1.5 px-3 py-1 bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 border border-cyan-500/40 rounded text-xs font-mono font-bold transition-all shadow-sm disabled:opacity-50"
        >
          {isAnalyzing ? (
            <>
              <RotateCw className="w-3.5 h-3.5 animate-spin" />
              <span>REASONING...</span>
            </>
          ) : (
            <>
              <Sparkles className="w-3.5 h-3.5" />
              <span>ANALYZE NETWORK</span>
            </>
          )}
        </button>
      </div>

      {/* Main Orchestrator Flow Container */}
      <div className="p-3.5 flex flex-col gap-3 font-mono text-xs text-slate-300 overflow-y-auto max-h-[520px]">
        {/* STAGE 1: OBSERVATION */}
        <div className="bg-[#121824] border border-slate-800 rounded p-2.5 flex flex-col gap-1.5">
          <div className="flex items-center justify-between text-[10px] text-cyan-400 font-bold uppercase tracking-wider">
            <span>1. OBSERVATION</span>
            <span className="text-slate-500 font-normal">SHA: {fingerprint.slice(0, 10)}...</span>
          </div>

          <div className="text-slate-200 text-xs font-sans font-medium line-clamp-2">
            {headline}
          </div>

          <div className="grid grid-cols-2 gap-2 mt-1 text-[11px] text-slate-400 border-t border-slate-800/80 pt-1.5">
            <div>
              <span>Deficit Nodes: </span>
              <span className="text-rose-400 font-bold">
                {observation?.demand?.deficit_node_count ?? 0}
              </span>
            </div>
            <div>
              <span>Idle Eligible: </span>
              <span className="text-emerald-400 font-bold">
                {observation?.fleet?.idle_eligible_pods ?? 0} pods
              </span>
            </div>
          </div>
        </div>

        <div className="flex justify-center text-slate-600 -my-1.5">
          <ArrowDown className="w-3.5 h-3.5" />
        </div>

        {/* STAGE 2: PROPOSAL */}
        <div className="bg-[#121824] border border-slate-800 rounded p-2.5 flex flex-col gap-1.5">
          <div className="flex items-center justify-between text-[10px] text-cyan-400 font-bold uppercase tracking-wider">
            <span>2. PROPOSAL</span>
            {proposal && (
              <span className="text-slate-400 text-[10px]">
                Confidence: <strong className="text-cyan-300">{(proposal.confidence * 100).toFixed(0)}%</strong>
              </span>
            )}
          </div>

          {proposal ? (
            <>
              <div className="flex items-center justify-between">
                <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-200 font-bold text-xs tracking-wider border border-slate-700">
                  {proposal.action_type}
                </span>
                {proposal.action_type === 'REQUEST_REBALANCING' && (
                  <span className="text-xs text-amber-400 font-bold">
                    {proposal.parameters?.target_node_id || 'Auto target'}
                  </span>
                )}
              </div>

              {/* WHY THIS ACTION? Toggle */}
              <button
                onClick={() => setShowWhyReasoning(!showWhyReasoning)}
                className="flex items-center justify-between w-full mt-1 px-2 py-1 bg-slate-900/80 hover:bg-slate-900 border border-slate-800 rounded text-[10px] text-slate-400 transition-colors"
              >
                <span className="flex items-center gap-1 text-cyan-400 font-semibold">
                  <Info className="w-3 h-3" />
                  WHY THIS ACTION?
                </span>
                {showWhyReasoning ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              </button>

              {showWhyReasoning && (
                <div className="p-2 bg-slate-950/70 border border-slate-800 rounded text-[11px] text-slate-300 font-sans leading-relaxed">
                  <p><strong className="text-slate-200 font-mono text-[10px] block mb-0.5 text-cyan-400">RATIONALE:</strong> {proposal.reason}</p>
                  <p className="mt-1.5"><strong className="text-slate-200 font-mono text-[10px] block mb-0.5 text-emerald-400">EXPECTED EFFECT:</strong> {proposal.expected_effect}</p>
                </div>
              )}
            </>
          ) : (
            <div className="text-xs text-slate-500 italic py-1">
              Click &quot;Analyze Network&quot; to prompt Gemini
            </div>
          )}
        </div>

        <div className="flex justify-center text-slate-600 -my-1.5">
          <ArrowDown className="w-3.5 h-3.5" />
        </div>

        {/* STAGE 3: DETERMINISTIC VALIDATOR */}
        <div
          className={`border rounded p-2.5 flex flex-col gap-1.5 transition-colors ${
            isApproved
              ? 'bg-emerald-950/20 border-emerald-800/60'
              : isRejected
              ? 'bg-rose-950/20 border-rose-800/60'
              : 'bg-[#121824] border-slate-800'
          }`}
        >
          <div className="flex items-center justify-between text-[10px] font-bold uppercase tracking-wider">
            <span className="text-cyan-400">3. DETERMINISTIC VALIDATOR</span>
            {lastRecord && (
              <span className="flex items-center gap-1">
                {isApproved ? (
                  <span className="text-emerald-400 font-bold flex items-center gap-1">
                    <ShieldCheck className="w-3 h-3" />
                    APPROVED
                  </span>
                ) : (
                  <span className="text-rose-400 font-bold flex items-center gap-1">
                    <ShieldAlert className="w-3 h-3" />
                    REJECTED
                  </span>
                )}
              </span>
            )}
          </div>

          {lastRecord ? (
            <div className="flex flex-col gap-1 text-[11px]">
              <div className="flex items-center justify-between text-slate-300">
                <span>Verification Suite:</span>
                <span className="font-bold text-slate-100">
                  {lastRecord.checks_passed} / {lastRecord.total_checks} checks passed
                </span>
              </div>

              {isRejected && (
                <div className="mt-1 p-2 bg-rose-950/50 border border-rose-800/80 rounded text-rose-300 text-[11px]">
                  <strong>REASON:</strong> {lastRecord.reason_code || 'Safety violation detected'}
                  {lastRecord.verdict_detail && <p className="mt-0.5 text-[10px] text-rose-400">{lastRecord.verdict_detail}</p>}
                </div>
              )}
            </div>
          ) : (
            <div className="text-xs text-slate-500 italic py-1">
              Awaiting proposal for deterministic validation
            </div>
          )}
        </div>

        <div className="flex justify-center text-slate-600 -my-1.5">
          <ArrowDown className="w-3.5 h-3.5" />
        </div>

        {/* STAGE 4: EXECUTION & RESULT */}
        <div className="bg-[#121824] border border-slate-800 rounded p-2.5 flex flex-col gap-1.5">
          <div className="flex items-center justify-between text-[10px] text-cyan-400 font-bold uppercase tracking-wider">
            <span>4. EXECUTION & RESULT</span>
            {lastRecord?.execution_status && (
              <span className="text-[10px] text-emerald-400 font-bold uppercase">
                {lastRecord.execution_status}
              </span>
            )}
          </div>

          {lastRecord?.result ? (
            <div className="flex flex-col gap-1 text-[11px] text-slate-300">
              <div className="flex items-center justify-between">
                <span>Dispatched Repositions:</span>
                <span className="font-bold text-cyan-300">
                  {lastRecord.result.repositionings_dispatched ?? 0}
                </span>
              </div>
              {lastRecord.result.deficit_before !== undefined && lastRecord.result.deficit_after !== undefined && (
                <div className="flex items-center justify-between">
                  <span>Deficit Impact:</span>
                  <span className="font-bold text-slate-100">
                    <span className="text-rose-400">{lastRecord.result.deficit_before.toFixed(1)}</span>
                    {' → '}
                    <span className="text-emerald-400">{lastRecord.result.deficit_after.toFixed(1)}</span>
                  </span>
                </div>
              )}
            </div>
          ) : (
            <div className="text-xs text-slate-500 italic py-1">
              Actions execute only after 100% validator approval
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
