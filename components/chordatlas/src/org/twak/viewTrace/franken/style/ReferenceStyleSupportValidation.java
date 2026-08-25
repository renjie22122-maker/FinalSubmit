package org.twak.viewTrace.franken.style;

import java.awt.Color;
import java.awt.image.BufferedImage;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.zip.CRC32;

import javax.imageio.ImageIO;

import org.twak.viewTrace.franken.FacadeTexApp;

/** Lightweight, CPU-only validation executable for reference-style contracts. */
public final class ReferenceStyleSupportValidation {

	private ReferenceStyleSupportValidation() {}

	public static void main( String[] args ) throws Exception {
		validateAtomicLatentCommit();
		validateImageInput();
		validateEncoderCapability();
		validateEncoderSerialization();
		validateSelectedBlockRoofPublication();
		validateReferenceClearAndPersistence();
		System.out.println( "ReferenceStyleSupport validation passed" );
	}

	private static void validateAtomicLatentCommit() {
		double[] destination = new double[] { 1, 2, 3, 4, 5, 6, 7, 8 };
		double[] before = Arrays.copyOf( destination, destination.length );
		double[] invalid = new double[] { 1, 2, 3, Double.NaN, 5, 6, 7, 8 };
		boolean rejected = false;
		try {
			ReferenceStyleSupport.commitLatent( destination, invalid );
		} catch ( IllegalArgumentException expected ) {
			rejected = true;
		}
		if ( !rejected || !Arrays.equals( destination, before ) )
			throw new AssertionError( "invalid latent must not partially change the live vector" );

		double[] valid = new double[] { -1, -2, -3, -4, -5, -6, -7, -8 };
		ReferenceStyleSupport.commitLatent( destination, valid );
		if ( !Arrays.equals( destination, valid ) )
			throw new AssertionError( "valid 8-dimensional latent was not committed" );
	}

	private static void validateImageInput() throws Exception {
		Path root = Files.createTempDirectory( "reference-style-image-" );
		try {
			BufferedImage image = new BufferedImage( 4, 3, BufferedImage.TYPE_INT_ARGB );
			image.setRGB( 1, 1, Color.orange.getRGB() );
			File png = root.resolve( "reference.png" ).toFile();
			ImageIO.write( image, "png", png );
			BufferedImage decoded = ReferenceStyleSupport.readReferenceImage( png );
			if ( decoded.getWidth() != 4 || decoded.getHeight() != 3 )
				throw new AssertionError( "valid reference image dimensions changed" );
			if ( ReferenceStyleSupport.toEncoderRgb( decoded ).getType() != BufferedImage.TYPE_3BYTE_BGR )
				throw new AssertionError( "reference image was not normalized to network RGB" );

			File invalid = root.resolve( "not-an-image.txt" ).toFile();
			Files.write( invalid.toPath(), "not an image".getBytes( StandardCharsets.UTF_8 ) );
			boolean rejected = false;
			try {
				ReferenceStyleSupport.readReferenceImage( invalid );
			} catch ( Exception expected ) {
				rejected = true;
			}
			if ( !rejected )
				throw new AssertionError( "non-image input must be rejected" );

			File oversizedDimensions = root.resolve( "oversized.png" ).toFile();
			writePngHeader( oversizedDimensions, 9000, 2 );
			rejected = false;
			try {
				ReferenceStyleSupport.readReferenceImage( oversizedDimensions );
			} catch ( Exception expected ) {
				rejected = expected.getMessage() != null
						&& expected.getMessage().contains( "size limit" );
			}
			if ( !rejected )
				throw new AssertionError( "oversized dimensions must be rejected before pixel decode" );
		} finally {
			deleteTree( root );
		}
	}

	private static void writePngHeader( File file, int width, int height ) throws Exception {
		byte[] type = new byte[] { 'I', 'H', 'D', 'R' };
		byte[] data = new byte[13];
		data[0] = (byte) ( width >>> 24 ); data[1] = (byte) ( width >>> 16 );
		data[2] = (byte) ( width >>> 8 ); data[3] = (byte) width;
		data[4] = (byte) ( height >>> 24 ); data[5] = (byte) ( height >>> 16 );
		data[6] = (byte) ( height >>> 8 ); data[7] = (byte) height;
		data[8] = 8; data[9] = 2;
		CRC32 crc = new CRC32();
		crc.update( type );
		crc.update( data );
		try ( DataOutputStream output = new DataOutputStream( new FileOutputStream( file ) ) ) {
			output.writeLong( 0x89504e470d0a1a0aL );
			output.writeInt( data.length );
			output.write( type );
			output.write( data );
			output.writeInt( (int) crc.getValue() );
		}
	}

	private static void validateEncoderCapability() throws Exception {
		Path root = Files.createTempDirectory( "reference-style-encoder-" );
		try {
			long now = 1_700_000_000_000L;
			String unavailable = ReferenceStyleSupport.encoderUnavailableReason(
					root.toFile(), "facade textures", 8, 8, now );
			if ( unavailable == null )
				throw new AssertionError( "encoder without watcher marker must be unavailable" );
			Path marker = root.resolve( ".myproject_frankengan_ready.json" );
			writeReadyMarker( marker, now );
			Files.createDirectories( root.resolve( "input" ).resolve( "facade textures_e" ).resolve( "val" ) );
			if ( ReferenceStyleSupport.encoderUnavailableReason(
					root.toFile(), "facade textures", 8, 8, now ) != null )
				throw new AssertionError( "ready 8-dimensional encoder was rejected" );
			if ( ReferenceStyleSupport.encoderUnavailableReason(
					root.toFile(), "facade textures", 7, 8, now ) == null )
				throw new AssertionError( "non-8-dimensional encoder was accepted" );

			writeReadyMarker( marker, now - 21_000L );
			Files.setLastModifiedTime( marker, FileTime.fromMillis( now ) );
			if ( ReferenceStyleSupport.encoderUnavailableReason(
					root.toFile(), "facade textures", 8, 8, now ) == null )
				throw new AssertionError( "stale heartbeat must be rejected even with fresh mtime" );

			writeReadyMarker( marker, now );
			Files.setLastModifiedTime( marker, FileTime.fromMillis( now - 21_000L ) );
			if ( ReferenceStyleSupport.encoderUnavailableReason(
					root.toFile(), "facade textures", 8, 8, now ) == null )
				throw new AssertionError( "stale marker mtime must be rejected" );
		} finally {
			deleteTree( root );
		}
	}

	private static void writeReadyMarker( Path marker, long heartbeatMillis ) throws Exception {
		String payload = "{\"pid\":42,\"token\":\"validation-token\",\"heartbeat_epoch\":"
				+ ( heartbeatMillis / 1000.0 ) + "}";
		Files.write( marker, payload.getBytes( StandardCharsets.UTF_8 ) );
		Files.setLastModifiedTime( marker, FileTime.fromMillis( heartbeatMillis ) );
	}

	private static void validateEncoderSerialization() {
		Object first = ReferenceStyleSupport.encoderLock( "facade textures" );
		Object second = ReferenceStyleSupport.encoderLock( "facade textures" );
		Object roof = ReferenceStyleSupport.encoderLock( "roof textures" );
		if ( first != second || first == roof )
			throw new AssertionError( "encoder locks must be global per network" );
	}

	private static void validateSelectedBlockRoofPublication() throws Exception {
		Path blockRoot = Files.createTempDirectory( "reference-style-roof-" );
		try {
			Path roof = blockRoot.resolve( "references" ).resolve( "roof" );
			Files.createDirectories( roof );
			Map<String, String> outputs = new LinkedHashMap<>();
			outputs.put( "satellite_north_up", "satellite_north_up.png" );
			outputs.put( "source_valid_mask", "source_valid_mask.png" );
			outputs.put( "footprint_mask", "footprint_mask.png" );
			outputs.put( "roof_style_mask", "roof_style_mask.png" );
			outputs.put( "roof_reference", "roof_reference.png" );
			outputs.put( "roof_reference_rgba", "roof_reference_rgba.png" );
			Map<String, String> hashes = new LinkedHashMap<>();
			for ( Map.Entry<String, String> output : outputs.entrySet() ) {
				File image = roof.resolve( output.getValue() ).toFile();
				ImageIO.write( new BufferedImage( 4, 4, BufferedImage.TYPE_3BYTE_BGR ), "png", image );
				hashes.put( output.getKey(), sha256ForValidation( image ) );
			}

			Files.write( roof.resolve( "reference.json" ), strictRoofManifest( "UNAVAILABLE", outputs, hashes )
					.getBytes( StandardCharsets.UTF_8 ) );
			if ( ReferenceStyleSupport.readyRoofReference( blockRoot.toFile() ) != null )
				throw new AssertionError( "UNAVAILABLE roof publication must not be offered" );
			Files.write( roof.resolve( "reference.json" ), strictRoofManifest( "READY", outputs, hashes )
					.getBytes( StandardCharsets.UTF_8 ) );
			File ready = ReferenceStyleSupport.readyRoofReference( blockRoot.toFile() );
			if ( ready == null || !ready.equals( roof.resolve( "roof_reference.png" ).toFile() ) )
				throw new AssertionError( "READY roof publication was not resolved within its block" );

			Map<String, String> incomplete = new LinkedHashMap<>( hashes );
			incomplete.remove( "roof_style_mask" );
			Files.write( roof.resolve( "reference.json" ), strictRoofManifest( "READY", outputs, incomplete )
					.getBytes( StandardCharsets.UTF_8 ) );
			if ( ReferenceStyleSupport.readyRoofReference( blockRoot.toFile() ) != null )
				throw new AssertionError( "READY roof publication requires a complete output_sha256" );

			Files.write( roof.resolve( "reference.json" ), strictRoofManifest( "READY", outputs, hashes )
					.getBytes( StandardCharsets.UTF_8 ) );
			Files.write( roof.resolve( "roof_reference.png" ), "tampered".getBytes( StandardCharsets.UTF_8 ) );
			if ( ReferenceStyleSupport.readyRoofReference( blockRoot.toFile() ) != null )
				throw new AssertionError( "roof reference hash mismatch must be rejected" );
		} finally {
			deleteTree( blockRoot );
		}
	}

	private static String strictRoofManifest( String status, Map<String, String> outputs,
			Map<String, String> hashes ) {
		StringBuilder out = new StringBuilder();
		out.append( "{\"schema_version\":1,\"kind\":\"myProject.appearance.roof_reference\",\"status\":\"" )
				.append( status ).append( "\",\"outputs\":{" );
		appendJsonMap( out, outputs );
		out.append( "},\"output_sha256\":{" );
		appendJsonMap( out, hashes );
		return out.append( "}}" ).toString();
	}

	private static void appendJsonMap( StringBuilder out, Map<String, String> values ) {
		boolean first = true;
		for ( Map.Entry<String, String> entry : values.entrySet() ) {
			if ( !first ) out.append( ',' );
			first = false;
			out.append( '\"' ).append( entry.getKey() ).append( "\":\"" )
					.append( entry.getValue() ).append( '\"' );
		}
	}

	private static String sha256ForValidation( File file ) throws Exception {
		MessageDigest digest = MessageDigest.getInstance( "SHA-256" );
		digest.update( Files.readAllBytes( file.toPath() ) );
		StringBuilder out = new StringBuilder();
		for ( byte value : digest.digest() )
			out.append( String.format( "%02x", value & 0xff ) );
		return out.toString();
	}

	private static void validateReferenceClearAndPersistence() throws Exception {
		GaussStyle style = new GaussStyle( FacadeTexApp.class );
		for ( int i = 0; i < style.mean.length; i++ )
			style.mean[i] = i + 0.25;
		double[] fallback = Arrays.copyOf( style.mean, style.mean.length );
		double[] encoded = new double[] { 8, 7, 6, 5, 4, 3, 2, 1 };
		style.applyReferenceVector( encoded, new BufferedImage( 2, 2, BufferedImage.TYPE_3BYTE_BGR ) );
		if ( !style.meanFromReference || !Arrays.equals( style.mean, encoded ) )
			throw new AssertionError( "reference vector was not applied" );
		style.clearReferenceVector();
		if ( style.meanFromReference || !Arrays.equals( style.mean, fallback ) )
			throw new AssertionError( "clear did not restore the pre-reference random distribution" );
		if ( !Modifier.isTransient( GaussStyle.class.getDeclaredField( "meanImage" ).getModifiers() ) )
			throw new AssertionError( "reference preview/source must not be persisted" );
	}

	private static void deleteTree( Path root ) throws Exception {
		if ( root == null || !Files.exists( root ) )
			return;
		Files.walk( root ).sorted( Comparator.reverseOrder() ).forEach( path -> {
			try {
				Files.deleteIfExists( path );
			} catch ( Exception error ) {
				throw new RuntimeException( error );
			}
		} );
	}
}
