import React from 'react';
import { render, screen } from '@testing-library/react';
import { axe } from 'jest-axe';
import Home from '../src/pages/index';

describe('Homepage', () => {
  it('renders the hero section', () => {
    render(<Home />);
    expect(screen.getByText('Physical AI Toolchain')).toBeDefined();
  });

  it('renders the Explore the platform section', () => {
    render(<Home />);
    expect(screen.getByText('Explore the platform')).toBeDefined();
  });

  it('renders the Deep dive section', () => {
    render(<Home />);
    expect(screen.getByText('Deep dive')).toBeDefined();
  });

  it('has one main and H1 with two labelled card sections', () => {
    render(<Home />);

    expect(screen.getAllByRole('main')).toHaveLength(1);
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    const explore = screen.getByRole('region', { name: 'Explore the platform' });
    const deepDive = screen.getByRole('region', { name: 'Deep dive' });
    expect(explore.querySelectorAll(':scope > ul')).toHaveLength(1);
    expect(deepDive.querySelectorAll(':scope > ul')).toHaveLength(1);
  });

  it('has no detectable accessibility violations', async () => {
    const { container } = render(<Home />);
    expect(await axe(container)).toHaveNoViolations();
  });
});
