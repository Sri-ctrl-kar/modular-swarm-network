import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { GeminiOrchestratorPanel } from '../panels/GeminiOrchestratorPanel';
import { OrchestrationObservationData, CycleAuditRecord } from '../types/api';

const mockObservation: OrchestrationObservationData = {
  time_min: 420.0,
  fingerprint: 'abcd1234ef567890abcd1234ef567890abcd1234ef567890abcd1234ef567890',
  headline: 'Residential South deficit 26.04; 20 idle pods eligible',
  network: {},
  demand: {
    deficit_node_count: 3,
    surplus_node_count: 5,
  },
  fleet: {
    idle_eligible_pods: 20,
    average_battery_percent: 88.5,
    minimum_battery_percent: 62.0,
    battery_distribution_percent_buckets: {},
  },
  swarm: {
    active_swarm_count: 2,
  },
  metrics: {},
  provenance: 'SYNTHETIC',
};

const mockApprovedRecord: CycleAuditRecord = {
  cycle_id: 'cycle-001',
  time_min: 420.0,
  observation_fingerprint: 'abcd1234ef567890',
  proposal: {
    action_type: 'REQUEST_REBALANCING',
    parameters: { target_node_id: 'residential_south' },
    reason: 'Heavy passenger demand detected at Residential South',
    expected_effect: 'Alleviate 12 pod deficit',
    confidence: 0.95,
    observation_fingerprint: 'abcd1234ef567890',
  },
  verdict: 'APPROVED',
  checks_passed: 15,
  total_checks: 15,
  execution_status: 'DISPATCHED',
  result: {
    deficit_before: 26.04,
    deficit_after: 14.04,
    repositionings_dispatched: 1,
  },
};

const mockRejectedRecord: CycleAuditRecord = {
  cycle_id: 'cycle-002',
  time_min: 421.0,
  observation_fingerprint: 'badfingerprint1234',
  proposal: {
    action_type: 'REQUEST_REBALANCING',
    parameters: { target_node_id: 'non_existent_node' },
    reason: 'Invalid node requested',
    expected_effect: 'None',
    confidence: 0.4,
    observation_fingerprint: 'badfingerprint1234',
  },
  verdict: 'REJECTED',
  reason_code: 'UNKNOWN_TARGET_NODE',
  verdict_detail: 'Node non_existent_node not found in network graph',
  checks_passed: 4,
  total_checks: 15,
};

describe('GeminiOrchestratorPanel component', () => {
  it('renders observation and mock AI indicator', () => {
    render(
      <GeminiOrchestratorPanel
        observation={mockObservation}
        lastRecord={null}
        providerType="mock"
        onRunCycle={vi.fn()}
        isAnalyzing={false}
      />
    );

    expect(screen.getByText(/gemini orchestrator/i)).toBeInTheDocument();
    expect(screen.getByText(/mock gemini/i)).toBeInTheDocument();
    expect(screen.getByText(/Residential South deficit 26.04/i)).toBeInTheDocument();
  });

  it('renders approved rebalancing proposal with checks and why reasoning', () => {
    render(
      <GeminiOrchestratorPanel
        observation={mockObservation}
        lastRecord={mockApprovedRecord}
        providerType="mock"
        onRunCycle={vi.fn()}
        isAnalyzing={false}
      />
    );

    expect(screen.getByText('REQUEST_REBALANCING')).toBeInTheDocument();
    expect(screen.getByText('APPROVED')).toBeInTheDocument();
    expect(screen.getByText('15 / 15 checks passed')).toBeInTheDocument();

    // Toggle WHY THIS ACTION
    const whyButton = screen.getByText(/why this action/i);
    fireEvent.click(whyButton);
    expect(screen.getByText(/Heavy passenger demand detected/i)).toBeInTheDocument();
  });

  it('renders visibly rejected action with deterministic reason code', () => {
    render(
      <GeminiOrchestratorPanel
        observation={mockObservation}
        lastRecord={mockRejectedRecord}
        providerType="mock"
        onRunCycle={vi.fn()}
        isAnalyzing={false}
      />
    );

    expect(screen.getByText('REJECTED')).toBeInTheDocument();
    expect(screen.getByText(/UNKNOWN_TARGET_NODE/i)).toBeInTheDocument();
  });

  it('shows LIVE GEMINI badge when providerType is live', () => {
    render(
      <GeminiOrchestratorPanel
        observation={mockObservation}
        lastRecord={null}
        providerType="live"
        onRunCycle={vi.fn()}
        isAnalyzing={false}
      />
    );

    expect(screen.getByText(/live gemini/i)).toBeInTheDocument();
  });
});
