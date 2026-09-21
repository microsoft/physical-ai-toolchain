// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import MDXComponents from '@theme-original/MDXComponents';
import BoxCard from '@site/src/components/BoxCard';
import CardGrid from '@site/src/components/CardGrid';
import IconCard from '@site/src/components/IconCard';

function Table(props: React.ComponentProps<'table'>): React.ReactElement {
  const caption = React.Children.toArray(props.children).find(
    (child) => React.isValidElement(child) && child.type === 'caption',
  );
  const captionText = React.isValidElement<{ children?: React.ReactNode }>(caption)
    ? React.Children.toArray(caption.props.children).join('').trim()
    : '';
  const headingId = props['aria-labelledby'];

  return (
    <div
      className="tableWrapper"
      role="group"
      aria-label={headingId ? undefined : `${captionText || props['aria-label'] || 'Table'}, scrollable table`}
      aria-labelledby={headingId}
      tabIndex={0}
    >
      <table {...props} />
    </div>
  );
}

function Input(props: React.ComponentProps<'input'>): React.ReactElement {
  return <input {...props} />;
}

function ListItem(props: React.ComponentProps<'li'>): React.ReactElement {
  const { children, className } = props;
  const taskLabelId = React.useId();
  const childrenArray = React.Children.toArray(children);
  const checkboxIndex = childrenArray.findIndex(
    (child) =>
      React.isValidElement<React.ComponentProps<'input'>>(child) &&
      child.props.type === 'checkbox' &&
      child.props.disabled,
  );

  if (!className?.split(/\s+/).includes('task-list-item') || checkboxIndex < 0) {
    return <li {...props} />;
  }

  const checkbox = childrenArray[checkboxIndex] as React.ReactElement<React.ComponentProps<'input'>>;
  const taskContent = childrenArray.filter((_, index) => index !== checkboxIndex);

  return (
    <li {...props}>
      {React.cloneElement(checkbox, { 'aria-labelledby': taskLabelId })}
      <span id={taskLabelId}>{taskContent}</span>
    </li>
  );
}

export default {
  ...MDXComponents,
  BoxCard,
  CardGrid,
  IconCard,
  input: Input,
  li: ListItem,
  table: Table,
};
