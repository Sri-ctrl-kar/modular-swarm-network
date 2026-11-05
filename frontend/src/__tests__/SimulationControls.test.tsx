import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { SimulationControls } from '../panels/SimulationControls';
import { ScenarioDefinition } from '../types/api';

const mockScenarios: ScenarioDefinition[] = [
  { id: 'baseline', name: 'Baseline City', description: 'Standard scenario', type: 'standard' },
  { id: 'peak_hour', name: 'Peak Hour', description: 'Peak surge', type: 'standard' },
];

describe('SimulationControls component', () => {
  it('triggers play and pause when clicked', () => {
    const onPlay = vi.fn();
    const onPause = vi.fn();

    const { rerender } = render(
      <SimulationControls
        isPlaying={false}
        onPlay={onPlay}
        onPause={onPause}
        onStep={vi.fn()}
        onReset={vi.fn()}
        speed={1}
        onSpeedChange={vi.fn()}
        scenarios={mockScenarios}
        currentScenarioId="baseline"
        onSelectScenario={vi.fn()}
        onTriggerRouteSwitch={vi.fn()}
        isRouteSwitchActive={false}
      />
    );

    const playBtn = screen.getByText('PLAY');
    fireEvent.click(playBtn);
    expect(onPlay).toHaveBeenCalledTimes(1);

    // Rerender as playing
    rerender(
      <SimulationControls
        isPlaying={true}
        onPlay={onPlay}
        onPause={onPause}
        onStep={vi.fn()}
        onReset={vi.fn()}
        speed={1}
        onSpeedChange={vi.fn()}
        scenarios={mockScenarios}
        currentScenarioId="baseline"
        onSelectScenario={vi.fn()}
        onTriggerRouteSwitch={vi.fn()}
        isRouteSwitchActive={false}
      />
    );

    const pauseBtn = screen.getByText('PAUSE');
    fireEvent.click(pauseBtn);
    expect(onPause).toHaveBeenCalledTimes(1);
  });

  it('triggers step and reset handlers', () => {
    const onStep = vi.fn();
    const onReset = vi.fn();

    render(
      <SimulationControls
        isPlaying={false}
        onPlay={vi.fn()}
        onPause={vi.fn()}
        onStep={onStep}
        onReset={onReset}
        speed={1}
        onSpeedChange={vi.fn()}
        scenarios={mockScenarios}
        currentScenarioId="baseline"
        onSelectScenario={vi.fn()}
        onTriggerRouteSwitch={vi.fn()}
        isRouteSwitchActive={false}
      />
    );

    fireEvent.click(screen.getByText('STEP'));
    expect(onStep).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText('RESET'));
    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it('changes speed multiplier', () => {
    const onSpeedChange = vi.fn();

    render(
      <SimulationControls
        isPlaying={false}
        onPlay={vi.fn()}
        onPause={vi.fn()}
        onStep={vi.fn()}
        onReset={vi.fn()}
        speed={1}
        onSpeedChange={onSpeedChange}
        scenarios={mockScenarios}
        currentScenarioId="baseline"
        onSelectScenario={vi.fn()}
        onTriggerRouteSwitch={vi.fn()}
        isRouteSwitchActive={false}
      />
    );

    fireEvent.click(screen.getByText('5x'));
    expect(onSpeedChange).toHaveBeenCalledWith(5);
  });
});
