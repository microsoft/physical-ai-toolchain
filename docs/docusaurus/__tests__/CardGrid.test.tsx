import React from 'react';
import { render } from '@testing-library/react';
import CardGrid from '../src/components/CardGrid';

describe('CardGrid', () => {
  it('renders children', () => {
    const { getByText } = render(
      <CardGrid>
        <div>Child A</div>
        <div>Child B</div>
      </CardGrid>,
    );
    expect(getByText('Child A')).toBeDefined();
    expect(getByText('Child B')).toBeDefined();
  });

  it('applies three-column class by default', () => {
    const { container } = render(
      <CardGrid>
        <div>Item</div>
      </CardGrid>,
    );
    expect(container.firstElementChild?.className).toBe('cardGrid');
  });

  it('applies two-column class', () => {
    const { container } = render(
      <CardGrid columns={2}>
        <div>Item</div>
      </CardGrid>,
    );
    expect(container.firstElementChild?.className).toBe('cardGridTwo');
  });

  it('applies four-column class', () => {
    const { container } = render(
      <CardGrid columns={4}>
        <div>Item</div>
      </CardGrid>,
    );
    expect(container.firstElementChild?.className).toBe('cardGridFour');
  });
});
