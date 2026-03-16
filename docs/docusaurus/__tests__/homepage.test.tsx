import React from 'react';
import { render, screen } from '@testing-library/react';
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
});
