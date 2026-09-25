import React from 'react';
import { render, screen } from '@testing-library/react';
import { axe } from 'jest-axe';
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

  it('renders every card as a direct list item', () => {
    render(
      <CardGrid>
        <article>Child A</article>
        <article>Child B</article>
      </CardGrid>,
    );

    const list = screen.getByRole('list');
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(Array.from(list.children).every((child) => child.tagName === 'LI')).toBe(true);
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

  it('has no detectable accessibility violations', async () => {
    const { container } = render(
      <CardGrid>
        <article>Accessible card</article>
      </CardGrid>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
