import React from 'react';
import { render, screen } from '@testing-library/react';
import { axe } from 'jest-axe';
import HeroSection from '../src/components/HeroSection';

describe('HeroSection', () => {
  it('renders the title', () => {
    render(<HeroSection title="Welcome" subtitle="Sub text" />);
    expect(screen.getByText('Welcome')).toBeDefined();
  });

  it('renders the subtitle', () => {
    render(<HeroSection title="Welcome" subtitle="Sub text" />);
    expect(screen.getByText('Sub text')).toBeDefined();
  });

  it('renders a section labelled by the title', () => {
    render(<HeroSection title="T" subtitle="S" />);
    expect(screen.getByRole('region', { name: 'T' })).toBeDefined();
    expect(screen.getByRole('heading', { level: 1, name: 'T' })).toBeDefined();
  });

  it('has no detectable accessibility violations', async () => {
    const { container } = render(<HeroSection title="Welcome" subtitle="Sub text" />);
    expect(await axe(container)).toHaveNoViolations();
  });
});
