// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import { Children, type ReactNode } from 'react';
import styles from '../styles.module.css';

export interface CardGridProps {
  children: ReactNode;
  columns?: 2 | 3 | 4;
}

export default function CardGrid({ children, columns = 3 }: CardGridProps): React.ReactElement {
  const columnClass: Record<number, string> = {
    2: styles.cardGridTwo,
    3: styles.cardGrid,
    4: styles.cardGridFour,
  };

  return (
    <ul className={columnClass[columns]}>
      {Children.map(children, (child) => (
        <li className={styles.cardGridItem}>{child}</li>
      ))}
    </ul>
  );
}
