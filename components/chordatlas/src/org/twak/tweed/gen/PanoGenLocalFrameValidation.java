package org.twak.tweed.gen;

import java.awt.Color;
import java.awt.image.BufferedImage;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import javax.vecmath.Vector3d;

import org.twak.tweed.Tweed;

/** Lightweight regression validation for manual PanoGen workspace-frame detection. */
public class PanoGenLocalFrameValidation {

	private static final Color SOUTH = Color.blue, WEST = Color.yellow,
			NORTH = Color.red, EAST = Color.green;

	private static void assertColour( Pano pano, BufferedImage image, float x, float z,
			Color expected, String direction ) {
		Color actual = new Color( pano.castTo( new float[] { x, 0, z }, image, null, null ) );
		if ( !actual.equals( expected ) )
			throw new AssertionError( direction + " sampled " + actual + " instead of " + expected );
	}

	private static void validateCastToDirections() {
		BufferedImage image = new BufferedImage( 360, 180, BufferedImage.TYPE_3BYTE_BGR );
		for ( int x = 0; x < image.getWidth(); x++ ) {
			int quadrant = ( ( x + 45 ) / 90 ) % 4;
			Color colour = quadrant == 0 ? SOUTH : quadrant == 1 ? WEST : quadrant == 2 ? NORTH : EAST;
			for ( int y = 0; y < image.getHeight(); y++ )
				image.setRGB( x, y, colour.getRGB() );
		}

		// PanoGen adds 180 degrees before constructing Pano.
		Pano local = new Pano( "local-validation", new Vector3d(), 180 + 180, 90, 0.001f );
		assertColour( local, image, 0, -10, NORTH, "myProject north (-Z)" );
		assertColour( local, image, 10, 0, EAST, "myProject east (+X)" );
		assertColour( local, image, 0, 10, SOUTH, "myProject south (+Z)" );
		assertColour( local, image, -10, 0, WEST, "myProject west (-X)" );

		Pano geographic = new Pano( "geographic-validation", new Vector3d(), 0 + 180, 90, 0.001f );
		assertColour( geographic, image, 0, 10, NORTH, "original north (+Z)" );
		assertColour( geographic, image, -10, 0, EAST, "original east (-X)" );
		assertColour( geographic, image, 0, -10, SOUTH, "original south (-Z)" );
		assertColour( geographic, image, 10, 0, WEST, "original west (+X)" );
	}

	private static void validateSnapshots( Path root ) throws Exception {
		if ( !Modifier.isVolatile( PanoGen.class.getDeclaredField( "panos" ).getModifiers() ) ||
				!Modifier.isVolatile( FeatureCache.class.getDeclaredField( "blockFeatures" ).getModifiers() ) )
			throw new AssertionError( "published panorama/feature snapshots must be volatile" );

		PanoGen generator = new PanoGen();
		AtomicReference<Throwable> failure = new AtomicReference<>();
		Thread writer = new Thread( () -> {
			try {
				for ( int i = 0; i < 10000; i++ )
					generator.panos = Collections.emptyList();
			} catch ( Throwable error ) {
				failure.set( error );
			}
		} );
		Thread reader = new Thread( () -> {
			try {
				for ( int i = 0; i < 10000; i++ ) {
					List<Pano> snapshot = generator.getPanos();
					if ( snapshot == null )
						throw new AssertionError( "panorama snapshot was null" );
					snapshot.size();
				}
			} catch ( Throwable error ) {
				failure.set( error );
			}
		} );
		writer.start(); reader.start(); writer.join(); reader.join();
		if ( failure.get() != null )
			throw new AssertionError( "concurrent panorama publication failed", failure.get() );

		Path features = Files.createDirectory( root.resolve( "features" ) );
		Path first = Files.createDirectory( features.resolve( "0_0" ) );
		FeatureCache cache = new FeatureCache( features.toFile() );
		Map<?, ?> oldSnapshot = cache.blockFeatures;
		Path second = Files.createDirectory( features.resolve( "1_1" ) );
		cache.refresh();
		if ( oldSnapshot.size() != 1 || cache.blockFeatures.size() != 2 || oldSnapshot == cache.blockFeatures )
			throw new AssertionError( "feature refresh did not publish an independent replacement map" );
		try {
			oldSnapshot.clear();
			throw new AssertionError( "published feature snapshot is mutable" );
		} catch ( UnsupportedOperationException expected ) {
			// expected
		}
		Files.delete( second ); Files.delete( first ); Files.delete( features );
	}

	private static void validatePersistedLayerMigration( Path root, Path manifest ) throws Exception {
		String previousData = Tweed.DATA;
		try {
			Tweed.DATA = root.toString();
			PanoGen generator = new PanoGen();
			generator.sourceCRS = Tweed.LAT_LONG;
			generator.meshPipelineLocalCoordinates = true;
			generator.localOriginLat = 1;
			generator.localOriginLon = 2;

			Files.write( manifest, (
					"{\"frame\":{\"origin_lat\":51.5,\"origin_lon\":-0.14," +
					"\"axes\":{\"x\":\"east\",\"y\":\"up\",\"z\":\"south\"}}}" )
					.getBytes( StandardCharsets.UTF_8 ) );
			generator.configureFromWorkspaceManifest();
			if ( !generator.meshPipelineLocalCoordinates ||
					Math.abs( generator.localOriginLat - 51.5 ) > 1e-12 ||
					Math.abs( generator.localOriginLon + 0.14 ) > 1e-12 )
				throw new AssertionError( "persisted PanoGen layer did not migrate to the current local frame" );

			Files.write( manifest, (
					"{\"frame\":{\"origin_lat\":51.5,\"origin_lon\":-0.14," +
					"\"axes\":{\"x\":\"west\",\"y\":\"up\",\"z\":\"north\"}}}" )
					.getBytes( StandardCharsets.UTF_8 ) );
			generator.configureFromWorkspaceManifest();
			if ( generator.meshPipelineLocalCoordinates ||
					!Double.isNaN( generator.localOriginLat ) || !Double.isNaN( generator.localOriginLon ) )
				throw new AssertionError( "invalid manifest retained stale local-frame state" );

			Files.delete( manifest );
			generator.meshPipelineLocalCoordinates = true;
			generator.localOriginLat = 3;
			generator.localOriginLon = 4;
			generator.configureFromWorkspaceManifest();
			if ( generator.meshPipelineLocalCoordinates ||
					!Double.isNaN( generator.localOriginLat ) || !Double.isNaN( generator.localOriginLon ) )
				throw new AssertionError( "missing manifest retained stale local-frame state" );
		} finally {
			Tweed.DATA = previousData;
		}
	}

	public static void main( String[] args ) throws Exception {
		Path root = Files.createTempDirectory( "pano-local-frame" );
		Path manifest = root.resolve( "manifest.json" );
		try {
			validateCastToDirections();
			validateSnapshots( root );
			validatePersistedLayerMigration( root, manifest );
			if ( PanoGen.detectWorkspaceLocalFrame( manifest.toFile() ) != null )
				throw new AssertionError( "missing manifest must use geographic fallback" );

			Files.write( manifest, (
					"{\"frame\":{\"origin_lat\":51.5,\"origin_lon\":-0.14," +
					"\"axes\":{\"x\":\"east\",\"y\":\"up\",\"z\":\"south\"}}}" )
					.getBytes( StandardCharsets.UTF_8 ) );
			PanoGen.WorkspaceLocalFrame frame = PanoGen.detectWorkspaceLocalFrame( manifest.toFile() );
			if ( frame == null || Math.abs( frame.originLat - 51.5 ) > 1e-12 ||
					Math.abs( frame.originLon + 0.14 ) > 1e-12 )
				throw new AssertionError( "valid myProject frame was not detected" );

			Files.write( manifest, (
					"{\"frame\":{\"origin_lat\":51.5,\"origin_lon\":-0.14," +
					"\"axes\":{\"x\":\"west\",\"y\":\"up\",\"z\":\"north\"}}}" )
					.getBytes( StandardCharsets.UTF_8 ) );
			if ( PanoGen.detectWorkspaceLocalFrame( manifest.toFile() ) != null )
				throw new AssertionError( "unexpected axes must use geographic fallback" );

			System.out.println( "PanoGen direction/local-frame/snapshot validation passed" );
		} finally {
			Files.deleteIfExists( manifest );
			Files.deleteIfExists( root );
		}
	}
}
