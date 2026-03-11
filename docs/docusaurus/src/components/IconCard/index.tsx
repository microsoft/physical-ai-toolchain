import React from 'react';
import type { ReactNode } from 'react';
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
  return (
    <article className={styles.card}>
      <div className={styles.iconCardLayout}>
        <div className={styles.iconContainer}>{icon}</div>
        <div className={styles.iconCardContent}>
          <span className={styles.supertitle}>{supertitle}</span>
          <Link className={styles.cardTitle} to={href}>
            {title}
          </Link>
          {description && <p className={styles.cardDescription}>{description}</p>}
        </div>
      </div>
    </article>
  );
}
