// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React, { useId } from 'react';
import styles from './styles.module.css';

export interface HeroSectionProps {
  title: string;
  subtitle: string;
}

export default function HeroSection({ title, subtitle }: HeroSectionProps): React.ReactElement {
  const titleId = useId();

  return (
    <section className={styles.hero} aria-labelledby={titleId}>
      <div className={styles.heroPattern} aria-hidden="true" />
      <div className={styles.heroContent}>
        <h1 className={styles.heroTitle} id={titleId}>
          {title}
        </h1>
        <p className={styles.heroSubtitle}>{subtitle}</p>
      </div>
    </section>
  );
}
