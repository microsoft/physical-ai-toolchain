// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React, { useId, type ReactNode } from 'react';
import clsx from 'clsx';
import { Collapsible, useCollapsible } from '@docusaurus/theme-common';
import TOCItems from '@theme-original/TOCItems';
import CollapseButton from '@theme-original/TOCCollapsible/CollapseButton';

import styles from './styles.module.css';

interface TocItem {
  readonly value: string;
  readonly id: string;
  readonly level: number;
}

interface Props {
  readonly toc: readonly TocItem[];
  readonly className?: string;
  readonly minHeadingLevel?: number;
  readonly maxHeadingLevel?: number;
}

export default function TOCCollapsible({
  toc,
  className,
  minHeadingLevel,
  maxHeadingLevel,
}: Props): ReactNode {
  const { collapsed, toggleCollapsed } = useCollapsible({ initialState: true });
  const buttonId = useId();
  const contentId = useId();

  return (
    <div
      className={clsx(
        styles.tocCollapsible,
        !collapsed && styles.tocCollapsibleExpanded,
        className,
      )}
    >
      <CollapseButton
        id={buttonId}
        collapsed={collapsed}
        aria-controls={contentId}
        aria-expanded={!collapsed}
        onClick={toggleCollapsed}
      />
      <div id={contentId} role="region" aria-labelledby={buttonId}>
        <Collapsible
          lazy={false}
          className={styles.tocCollapsibleContent}
          collapsed={collapsed}
        >
          <TOCItems
            toc={toc}
            minHeadingLevel={minHeadingLevel}
            maxHeadingLevel={maxHeadingLevel}
          />
        </Collapsible>
      </div>
    </div>
  );
}