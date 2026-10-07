import React, { useEffect, useRef, useState } from 'react';
import { Animated, Easing, Image, PanResponder, Pressable, StyleSheet, Text, View } from 'react-native';
import { animationStates } from './animation-states';
const sheets = { robot: require('../assets/robot.png'), cat: require('../assets/cat.png'), knight: require('../assets/knight.png') };
const spriteSize = 128;
export function Pet({ character, onGreeting }: { character: keyof typeof sheets; onGreeting: () => void }) {
  const [state, setState] = useState('WAVE'), [frame, setFrame] = useState(0), [hidden, setHidden] = useState(false);
  const position = useRef(new Animated.ValueXY()).current;
  const greeting = useRef(onGreeting); greeting.current = onGreeting;
  const [dragging, setDragging] = useState(false);
  const releaseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const gesture = useRef(PanResponder.create({ onStartShouldSetPanResponder: () => true,
    onPanResponderGrant: () => { position.stopAnimation(); setDragging(true); setState('DRAG'); },
    onPanResponderMove: (_, gesture) => position.setValue({ x: Math.max(-110, Math.min(110, gesture.dx)), y: Math.max(-65, Math.min(30, gesture.dy)) }),
    onPanResponderRelease: (_, gesture) => {
      setDragging(false);
      if (Math.abs(gesture.dx) + Math.abs(gesture.dy) < 8) { setState('WAVE'); greeting.current(); }
      else setState('PARACHUTE');
      Animated.timing(position, { toValue: { x: 0, y: 0 }, duration: 1500, easing: Easing.out(Easing.quad), useNativeDriver: false }).start(() => setState('LANDING'));
    }, onPanResponderTerminate: () => { setDragging(false); position.setValue({ x: 0, y: 0 }); setState('IDLE'); }
  })).current;
  // PanResponder retains the initial callback; keep a current greeting behind it.
  useEffect(() => {
    const timer = setInterval(() => setFrame(f => (f + 1) % 8), 120);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (dragging || hidden) return;
    const timer = setInterval(() => {
      const options = animationStates.filter(s => !['PARACHUTE', 'DRAG', 'LANDING', 'HOP'].includes(s));
      setState(options[Math.floor(Math.random() * options.length)]);
    }, 4200);
    return () => clearInterval(timer);
  }, [dragging, hidden]);
  useEffect(() => () => { if (releaseTimer.current) clearTimeout(releaseTimer.current); position.stopAnimation(); }, [position]);
  const index = Math.max(0, animationStates.indexOf(state));
  return <View style={styles.hero}>
    <View style={styles.stage}>
    <View pointerEvents="none" style={styles.aura} />
    <View pointerEvents="none" style={styles.orbit} />
    <Animated.View {...gesture.panHandlers} style={[styles.sprite, { transform: position.getTranslateTransform(), opacity: hidden ? 0 : 1 }]} accessibilityLabel={`Jeffery ${character}, ${state.toLowerCase()}`}>
      <Image source={sheets[character]} style={{ position: 'absolute', width: spriteSize * 8, height: animationStates.length * spriteSize, left: -frame * spriteSize, top: -index * spriteSize }} resizeMode="stretch" />
    </Animated.View>
    {hidden && <Pressable accessibilityRole="button" onPress={() => { setHidden(false); setState('PEEK'); }} style={styles.hideout}><Text style={styles.buttonText}>📁 Peek out</Text></Pressable>}
    </View>
    <Text style={styles.hint}>{hidden ? 'Jeffery is in his little in-app hideout.' : 'Drag Jeffery for a parachute landing'}</Text>
    <View style={styles.controls}>{['Wave', 'Dance', 'Hide'].map(label => <Pressable accessibilityRole="button" key={label} style={styles.button} onPress={() => {
      if (label === 'Hide') { setHidden(true); if (releaseTimer.current) clearTimeout(releaseTimer.current); releaseTimer.current = setTimeout(() => { setHidden(false); setState('PEEK'); }, 6000); }
      else { setHidden(false); setState(label === 'Wave' ? 'WAVE' : 'DANCE'); if (label === 'Wave') greeting.current(); }
    }}><Text style={styles.buttonText}>{label}</Text></Pressable>)}</View>
  </View>;
}
const styles = StyleSheet.create({ hero: { alignItems: 'center', paddingTop: 8, paddingBottom: 10 }, stage: { width: 164, height: 132, alignItems: 'center', justifyContent: 'center' }, sprite: { width: spriteSize, height: spriteSize, overflow: 'hidden' },
  aura: { position: 'absolute', width: 112, height: 112, top: 9, borderRadius: 56, backgroundColor: 'rgba(92, 225, 205, 0.045)', borderWidth: 1, borderColor: 'rgba(128, 241, 223, 0.09)' },
  orbit: { position: 'absolute', width: 124, height: 28, bottom: 0, borderRadius: 62, backgroundColor: 'rgba(68, 198, 189, 0.08)', borderWidth: 1, borderColor: 'rgba(126, 237, 222, 0.18)' },
  hint: { color: '#96a8bc', fontSize: 12, marginTop: 8 }, controls: { flexDirection: 'row', gap: 8, marginTop: 14 },
  button: { backgroundColor: '#243344', paddingVertical: 9, paddingHorizontal: 20, borderRadius: 18 }, buttonText: { color: '#b8ead9', fontWeight: '600' }, hideout: { position: 'absolute', inset: 0, justifyContent: 'center', alignItems: 'center' } });
