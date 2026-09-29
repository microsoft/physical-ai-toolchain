// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import { type ReactNode, useId } from 'react';
import Link from '@docusaurus/Link';
import styles from '../styles.module.css';

export interface IconCardProps {
  icon: ReactNode;
  supertitle: string;
  title: string;
  href: string;
  description?: string;
}

export default function IconCard({ icon, supertitle, title, href, description }: IconCardProps): React.ReactElement {
  const titleId = useId();

  return (
    <article className={styles.card} aria-labelledby={titleId}>
      <div className={styles.iconCardLayout}>
        <div className={styles.iconContainer} aria-hidden="true">
          {icon}
        </div>
        <div className={styles.iconCardContent}>
          <span className={styles.supertitle}>{supertitle}</span>
          <h3 className={styles.cardTitle} id={titleId}>
            <Link to={href}>{title}</Link>
          </h3>
          {description && <p className={styles.cardDescription}>{description}</p>}
        </div>
      </div>
    </article>
  );
}
