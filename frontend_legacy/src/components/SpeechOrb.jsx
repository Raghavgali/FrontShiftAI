import { useEffect, useRef } from 'react';
import * as THREE from 'three';

const SimplexNoise = () => {
  const grad3 = [
    [1,1,0],[-1,1,0],[1,-1,0],[-1,-1,0],
    [1,0,1],[-1,0,1],[1,0,-1],[-1,0,-1],
    [0,1,1],[0,-1,1],[0,1,-1],[0,-1,-1]
  ];
  
  const p = [];
  for (let i = 0; i < 256; i++) p[i] = Math.floor(Math.random() * 256);
  const perm = new Array(512);
  for (let i = 0; i < 512; i++) perm[i] = p[i & 255];

  const dot3 = (g, x, y, z) => g[0]*x + g[1]*y + g[2]*z;

  const noise3D = (xin, yin, zin) => {
    const F3 = 1/3, G3 = 1/6;
    let n0, n1, n2, n3;
    const s = (xin + yin + zin) * F3;
    const i = Math.floor(xin + s);
    const j = Math.floor(yin + s);
    const k = Math.floor(zin + s);
    const t = (i + j + k) * G3;
    const X0 = i - t, Y0 = j - t, Z0 = k - t;
    const x0 = xin - X0, y0 = yin - Y0, z0 = zin - Z0;
    
    let i1, j1, k1, i2, j2, k2;
    if (x0 >= y0) {
      if (y0 >= z0) { i1=1; j1=0; k1=0; i2=1; j2=1; k2=0; }
      else if (x0 >= z0) { i1=1; j1=0; k1=0; i2=1; j2=0; k2=1; }
      else { i1=0; j1=0; k1=1; i2=1; j2=0; k2=1; }
    } else {
      if (y0 < z0) { i1=0; j1=0; k1=1; i2=0; j2=1; k2=1; }
      else if (x0 < z0) { i1=0; j1=1; k1=0; i2=0; j2=1; k2=1; }
      else { i1=0; j1=1; k1=0; i2=1; j2=1; k2=0; }
    }
    
    const x1 = x0 - i1 + G3, y1 = y0 - j1 + G3, z1 = z0 - k1 + G3;
    const x2 = x0 - i2 + 2*G3, y2 = y0 - j2 + 2*G3, z2 = z0 - k2 + 2*G3;
    const x3 = x0 - 1 + 3*G3, y3 = y0 - 1 + 3*G3, z3 = z0 - 1 + 3*G3;
    
    const ii = i & 255, jj = j & 255, kk = k & 255;
    const gi0 = perm[ii + perm[jj + perm[kk]]] % 12;
    const gi1 = perm[ii + i1 + perm[jj + j1 + perm[kk + k1]]] % 12;
    const gi2 = perm[ii + i2 + perm[jj + j2 + perm[kk + k2]]] % 12;
    const gi3 = perm[ii + 1 + perm[jj + 1 + perm[kk + 1]]] % 12;
    
    let t0 = 0.6 - x0*x0 - y0*y0 - z0*z0;
    n0 = t0 < 0 ? 0 : (t0 *= t0, t0 * t0 * dot3(grad3[gi0], x0, y0, z0));
    let t1 = 0.6 - x1*x1 - y1*y1 - z1*z1;
    n1 = t1 < 0 ? 0 : (t1 *= t1, t1 * t1 * dot3(grad3[gi1], x1, y1, z1));
    let t2 = 0.6 - x2*x2 - y2*y2 - z2*z2;
    n2 = t2 < 0 ? 0 : (t2 *= t2, t2 * t2 * dot3(grad3[gi2], x2, y2, z2));
    let t3 = 0.6 - x3*x3 - y3*y3 - z3*z3;
    n3 = t3 < 0 ? 0 : (t3 *= t3, t3 * t3 * dot3(grad3[gi3], x3, y3, z3));
    
    return 32 * (n0 + n1 + n2 + n3);
  };

  return { noise3D };
};

const lerp = (a, b, t) => a + (b - a) * t;

/**
 * SpeechOrb Component
 * A 3D animated wireframe orb using Perlin noise displacement
 * 
 * @param {string} status - One of: 'idle' | 'listening' | 'processing' | 'speaking'
 * @param {number} size - Size of the orb in pixels (default: 300)
 */
const SpeechOrb = ({ status = 'idle', size = 300 }) => {
  const containerRef = useRef(null);
  const rendererRef = useRef(null);
  const frameRef = useRef(null);
  const noiseRef = useRef(null);
  const originalPositionsRef = useRef(null);
  const displacementRef = useRef(null);
  
  const currentParamsRef = useRef({
    intensity: 0.3,
    noiseScale: 0.8,
    noiseAmount: 0.06,
    rotationSpeed: 0.025,
    waveSpeed: 0.3
  });

  useEffect(() => {
    if (!containerRef.current) return;

    noiseRef.current = SimplexNoise();

    const scene = new THREE.Scene();

    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000);
    camera.position.z = 4;

    const renderer = new THREE.WebGLRenderer({ 
      antialias: true, 
      alpha: true,
      powerPreference: "high-performance"
    });
    renderer.setSize(size, size);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    containerRef.current.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // Use lower detail sphere for visible wireframe structure
    const geometry = new THREE.IcosahedronGeometry(1.2, 24);
    
    originalPositionsRef.current = geometry.attributes.position.array.slice();
    
    const vertexCount = geometry.attributes.position.count;
    displacementRef.current = new Float32Array(vertexCount);
    geometry.setAttribute('aDisplacement', new THREE.BufferAttribute(displacementRef.current, 1));

    // Points material - smaller, more defined points
    const pointsMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 },
        uIntensity: { value: 0.5 }
      },
      vertexShader: `
        attribute float aDisplacement;
        varying float vDisplacement;
        varying vec3 vPosition;
        varying float vDepth;
        
        void main() {
          vPosition = position;
          vDisplacement = aDisplacement;
          vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
          vDepth = -mvPosition.z;
          gl_Position = projectionMatrix * mvPosition;
          // Smaller point size for defined dots
          gl_PointSize = max(1.5, 3.0 * (1.0 / -mvPosition.z));
        }
      `,
      fragmentShader: `
        varying vec3 vPosition;
        varying float vDepth;
        varying float vDisplacement;
        
        void main() {
          vec2 center = gl_PointCoord - vec2(0.5);
          float dist = length(center);
          if (dist > 0.5) discard;
          
          // Metallic silver palette
          vec3 darkGray = vec3(0.42, 0.45, 0.5);      // #6B7280 - peaks
          vec3 silver = vec3(0.61, 0.64, 0.69);       // #9CA3AF - mid
          vec3 brightSilver = vec3(0.91, 0.91, 0.91); // #E8E8E8 - valleys
          
          float t = clamp(vDisplacement * 2.5 + 0.5, 0.0, 1.0);
          
          vec3 color;
          if (t > 0.5) {
            color = mix(silver, darkGray, (t - 0.5) * 2.0);
          } else {
            color = mix(brightSilver, silver, t * 2.0);
          }
          
          float whiteness = smoothstep(0.3, 0.0, t);
          color = mix(color, vec3(0.95), whiteness * 0.4);
          
          // Depth-based opacity - back faces more transparent
          float depthFade = smoothstep(2.5, 4.5, vDepth);
          float opacity = mix(0.9, 0.3, depthFade);
          
          // Soft circle falloff
          opacity *= smoothstep(0.5, 0.2, dist);
          
          gl_FragColor = vec4(color, opacity);
        }
      `,
      transparent: true,
      depthWrite: false,
      depthTest: true,
      blending: THREE.NormalBlending
    });

    const points = new THREE.Points(geometry, pointsMaterial);
    scene.add(points);

    // Wireframe - more visible
    const wireMaterial = new THREE.ShaderMaterial({
      uniforms: {},
      vertexShader: `
        attribute float aDisplacement;
        varying float vDisplacement;
        varying vec3 vPosition;
        varying float vDepth;
        void main() {
          vPosition = position;
          vDisplacement = aDisplacement;
          vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
          vDepth = -mvPosition.z;
          gl_Position = projectionMatrix * mvPosition;
        }
      `,
      fragmentShader: `
        varying vec3 vPosition;
        varying float vDisplacement;
        varying float vDepth;
        void main() {
          vec3 darkGray = vec3(0.42, 0.45, 0.5);
          vec3 brightSilver = vec3(0.75, 0.75, 0.75);
          
          float t = clamp(vDisplacement * 2.5 + 0.5, 0.0, 1.0);
          vec3 color = mix(brightSilver, darkGray, t);
          
          // Depth-based opacity for wireframe
          float depthFade = smoothstep(2.5, 4.5, vDepth);
          float opacity = mix(0.25, 0.05, depthFade);
          
          gl_FragColor = vec4(color, opacity);
        }
      `,
      transparent: true,
      wireframe: true,
      depthWrite: false,
      depthTest: true
    });
    
    const wireframe = new THREE.Mesh(geometry, wireMaterial);
    scene.add(wireframe);

    // Subtle outer glow
    const glowGeometry = new THREE.SphereGeometry(1.6, 32, 32);
    const glowMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uIntensity: { value: 0.5 }
      },
      vertexShader: `
        varying vec3 vNormal;
        void main() {
          vNormal = normalize(normalMatrix * normal);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform float uIntensity;
        varying vec3 vNormal;
        void main() {
          float intensity = pow(0.65 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 3.0);
          vec3 glowColor = vec3(0.7, 0.7, 0.72);
          gl_FragColor = vec4(glowColor, intensity * 0.12 * uIntensity);
        }
      `,
      transparent: true,
      side: THREE.BackSide,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    });
    const glowMesh = new THREE.Mesh(glowGeometry, glowMaterial);
    scene.add(glowMesh);

    let time = 0;

    const animate = () => {
      time += 0.006;
      
      // Target parameters based on status
      let targetParams;
      switch(status) {
        case 'listening':
          targetParams = {
            intensity: 1.0,
            noiseScale: 1.4,
            noiseAmount: 0.22,
            rotationSpeed: 0.06,
            waveSpeed: 0.7
          };
          break;
        case 'speaking':
          targetParams = {
            intensity: 1.2,
            noiseScale: 1.6,
            noiseAmount: 0.28,
            rotationSpeed: 0.08,
            waveSpeed: 0.9
          };
          break;
        case 'processing':
          targetParams = {
            intensity: 0.6,
            noiseScale: 1.0,
            noiseAmount: 0.1,
            rotationSpeed: 0.04,
            waveSpeed: 0.4
          };
          break;
        default: // idle
          targetParams = {
            intensity: 0.3,
            noiseScale: 0.8,
            noiseAmount: 0.05,
            rotationSpeed: 0.02,
            waveSpeed: 0.25
          };
      }
      
      // Smooth interpolation towards target
      const lerpFactor = 0.03;
      const curr = currentParamsRef.current;
      curr.intensity = lerp(curr.intensity, targetParams.intensity, lerpFactor);
      curr.noiseScale = lerp(curr.noiseScale, targetParams.noiseScale, lerpFactor);
      curr.noiseAmount = lerp(curr.noiseAmount, targetParams.noiseAmount, lerpFactor);
      curr.rotationSpeed = lerp(curr.rotationSpeed, targetParams.rotationSpeed, lerpFactor);
      curr.waveSpeed = lerp(curr.waveSpeed, targetParams.waveSpeed, lerpFactor);

      glowMaterial.uniforms.uIntensity.value = curr.intensity;

      const positions = geometry.attributes.position.array;
      const original = originalPositionsRef.current;
      const displacement = displacementRef.current;
      const noise = noiseRef.current;

      for (let i = 0; i < positions.length; i += 3) {
        const idx = i / 3;
        const ox = original[i];
        const oy = original[i + 1];
        const oz = original[i + 2];

        // Layered noise for smooth organic waves
        const n1 = noise.noise3D(
          ox * curr.noiseScale + time * curr.waveSpeed,
          oy * curr.noiseScale + time * curr.waveSpeed * 0.7,
          oz * curr.noiseScale + time * curr.waveSpeed * 0.5
        );
        
        const n2 = noise.noise3D(
          ox * curr.noiseScale * 1.8 + time * curr.waveSpeed * 1.3,
          oy * curr.noiseScale * 1.8 - time * curr.waveSpeed * 0.5,
          oz * curr.noiseScale * 1.8 + time * curr.waveSpeed * 0.3
        ) * 0.35;
        
        const n3 = noise.noise3D(
          ox * curr.noiseScale * 0.5 + time * curr.waveSpeed * 0.2,
          oy * curr.noiseScale * 0.5 + time * curr.waveSpeed * 0.25,
          oz * curr.noiseScale * 0.5 - time * curr.waveSpeed * 0.15
        ) * 0.5;

        const noiseVal = (n1 + n2 + n3) / 1.85;

        const length = Math.sqrt(ox * ox + oy * oy + oz * oz);
        const nx = ox / length;
        const ny = oy / length;
        const nz = oz / length;

        const displacementValue = noiseVal * curr.noiseAmount;
        displacement[idx] = noiseVal;
        
        positions[i] = ox + nx * displacementValue;
        positions[i + 1] = oy + ny * displacementValue;
        positions[i + 2] = oz + nz * displacementValue;
      }

      geometry.attributes.position.needsUpdate = true;
      geometry.attributes.aDisplacement.needsUpdate = true;
      geometry.computeVertexNormals();

      points.rotation.y += curr.rotationSpeed * 0.05;
      points.rotation.x = Math.sin(time * 0.3) * 0.06;
      wireframe.rotation.copy(points.rotation);
      glowMesh.rotation.copy(points.rotation);

      renderer.render(scene, camera);
      frameRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
      if (rendererRef.current && containerRef.current) {
        containerRef.current.removeChild(rendererRef.current.domElement);
      }
      geometry.dispose();
      pointsMaterial.dispose();
      wireMaterial.dispose();
      glowMaterial.dispose();
      glowGeometry.dispose();
      renderer.dispose();
    };
  }, [status, size]);

  return (
    <div 
      ref={containerRef} 
      className="relative"
      style={{ width: size, height: size }}
    />
  );
};

export default SpeechOrb;
