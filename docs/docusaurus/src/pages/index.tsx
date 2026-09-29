// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import Layout from '@theme/Layout';
import HeroSection from '../components/HeroSection';
import CardGrid from '../components/CardGrid';
import IconCard from '../components/IconCard';
import BoxCard from '../components/BoxCard';
import { iconCards, boxCards } from '../data/hubCards';
import styles from './index.module.css';

export default function Home(): React.ReactElement {
  return (
    <Layout>
      <main>
        <HeroSection
          title="Physical AI Toolchain"
          subtitle="Production-ready framework for training, deploying, and operating physical AI solutions on Azure with NVIDIA Isaac."
        />
        <section className={styles.section} aria-labelledby="explore-platform-heading">
          <h2 id="explore-platform-heading">Explore the platform</h2>
          <CardGrid columns={3}>
            {iconCards.map((card) => (
              <IconCard key={card.href} {...card} />
            ))}
          </CardGrid>
        </section>
        <section
          className={`${styles.section} ${styles.secondarySection}`}
          aria-labelledby="deep-dive-heading"
        >
          <h2 id="deep-dive-heading">Deep dive</h2>
          <CardGrid columns={4}>
            {boxCards.map((card) => (
              <BoxCard key={card.title} {...card} />
            ))}
          </CardGrid>
        </section>
      </main>
    </Layout>
  );
}
