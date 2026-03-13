import React from 'react';
import { render, screen } from '@testing-library/react';
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

  it('renders a header element', () => {
    const { container } = render(<HeroSection title="T" subtitle="S" />);
    expect(container.querySelector('header')).not.toBeNull();
  });
});
